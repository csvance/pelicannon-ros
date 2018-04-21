#!/usr/bin/env python

import datetime
from collections import deque
from cv_bridge import CvBridge
from threading import Lock

import cv2
import rospy
from pelicannon.msg import CategorizedRegionOfInterest, CategorizedRegionsOfInterest, AngleDeltaImage
from sensor_msgs.msg import Image, Imu

from tracker import BodyTrackerPipeline, MotionTrackerPipeline


class ObjectDetectorNode(object):
    def __init__(self, debug=False):
        self._debug = debug
        self._initialize_pipelines()
        self._cv_br = CvBridge()

        self._ahrs_lock = Lock()
        self._ahrs = None

        self._imu_lock = Lock()
        self._imu_queue = deque(maxlen=3)

        self._last_image = None

        self._ros_init()

    def _ros_init(self):
        rospy.init_node('object_detector')
        self._publisher = rospy.Publisher('regions_of_interest', CategorizedRegionsOfInterest, queue_size=10)

        if rospy.get_param('object_detector/debug'):
            self._angle_delta_image_publisher = rospy.Publisher('angle_delta_image', AngleDeltaImage, queue_size=100)

        rospy.Subscriber("/webcam/image_raw", Image, self._camera_callback)
        rospy.Subscriber("/imu/data", Imu, self._imu_callback)

    def _initialize_pipelines(self):
        if rospy.get_param('object_detector/body_regions'):
            self._body_tracker = BodyTrackerPipeline()
        if rospy.get_param('object_detector/motion_regions'):
            self._motion_detector = MotionTrackerPipeline()

    def _imu_callback(self, imu):
        self._imu_lock.acquire()
        self._imu_queue.append(imu)
        self._imu_lock.release()

    def _compute_angular_velocity(self):
        a_x = 0.
        a_y = 0.
        a_z = 0.

        self._imu_lock.acquire()

        for i in self._imu_queue:
            a_x += i.angular_velocity.x
            a_y += i.angular_velocity.y
            a_z += i.angular_velocity.z

        a_x /= len(self._imu_queue)
        a_y /= len(self._imu_queue)
        a_z /= len(self._imu_queue)

        self._imu_lock.release()

        return a_x, a_y, a_z

    def _camera_callback(self, image):

        delta_t = (image.header.stamp - self._last_image.header.stamp).to_sec() if self._last_image is not None else 0.

        time_i = datetime.datetime.now()

        frame = self._cv_br.imgmsg_to_cv2(image, desired_encoding="passthrough")

        # Create grayscale versions
        frame_grayscale = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Does this improve performance? In example code in OpenCV documentation.
        frame_grayscale = cv2.equalizeHist(frame_grayscale)

        if rospy.get_param('object_detector/debug'):
            self._imu_lock.acquire()
            last_imu = [i for i in self._imu_queue]
            self._imu_lock.release()

            adi = AngleDeltaImage(image, self._last_image, last_imu)
            self._angle_delta_image_publisher.publish(adi)

        av_x, av_y, av_z = self._compute_angular_velocity()

        # Send data down pipeline and process results
        regions = []

        if rospy.get_param('object_detector/motion_regions'):
            motion_regions = self._motion_detector.process_frame(frame_grayscale,
                                                                 phi=av_z * delta_t
                                                                 if len(self._imu_queue) != 0 else None)
            for region in motion_regions:
                regions.append(
                    CategorizedRegionOfInterest(x=region.x, y=region.y, w=region.w, h=region.h, category='motion'))

        if rospy.get_param('object_detector/body_regions'):
            haarcascade_regions = self._body_tracker.process_frame(frame_grayscale)
            for region in haarcascade_regions:
                regions.append(
                    CategorizedRegionOfInterest(x=region.x, y=region.y, w=region.w, h=region.h, category='body'))

        self._publisher.publish(CategorizedRegionsOfInterest(regions=regions))

        time_f = datetime.datetime.now()

        if self._debug:
            rospy.loginfo(rospy.get_caller_id() + "Frame processed in %s", time_f - time_i)

        self._last_image = image


if __name__ == '__main__':
    detector = ObjectDetectorNode()
    rospy.spin()
