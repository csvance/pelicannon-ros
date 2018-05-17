import math
from cv_bridge import CvBridge

import cv2
import numpy as np
import rospy
from sensor_msgs.msg import Image


class ImageTransformer(object):

    def __init__(self, image):
        self.image = image

        self.height = self.image.shape[0]
        self.width = self.image.shape[1]
        self.num_channels = self.image.shape[2]

    """ Wrapper of Rotating a Image """

    def rotate_along_axis(self, theta=0, phi=0, gamma=0, dx=0, dy=0, dz=0):
        # Get ideal focal length on z axis
        # NOTE: Change this section to other axis if needed
        d = np.sqrt(self.height ** 2 + self.width ** 2)
        self.focal = d / (2 * np.sin(gamma) if np.sin(gamma) != 0 else 1)
        dz = self.focal

        # Get projection matrix
        mat = self.get_M(theta, phi, gamma, dx, dy, dz)

        return cv2.warpPerspective(self.image.copy(), mat, (self.width, self.height))

    """ Get Perspective Projection Matrix """

    def get_M(self, theta, phi, gamma, dx, dy, dz):
        w = self.width
        h = self.height
        f = self.focal

        # Projection 2D -> 3D matrix
        A1 = np.array([[1, 0, -w / 2],
                       [0, 1, -h / 2],
                       [0, 0, 1],
                       [0, 0, 1]])

        # Rotation matrices around the X, Y, and Z axis
        RX = np.array([[1, 0, 0, 0],
                       [0, np.cos(theta), -np.sin(theta), 0],
                       [0, np.sin(theta), np.cos(theta), 0],
                       [0, 0, 0, 1]])

        RY = np.array([[np.cos(phi), 0, -np.sin(phi), 0],
                       [0, 1, 0, 0],
                       [np.sin(phi), 0, np.cos(phi), 0],
                       [0, 0, 0, 1]])

        RZ = np.array([[np.cos(gamma), -np.sin(gamma), 0, 0],
                       [np.sin(gamma), np.cos(gamma), 0, 0],
                       [0, 0, 1, 0],
                       [0, 0, 0, 1]])

        # Composed rotation matrix with (RX, RY, RZ)
        R = np.dot(np.dot(RX, RY), RZ)

        # Translation matrix
        T = np.array([[1, 0, 0, dx],
                      [0, 1, 0, dy],
                      [0, 0, 1, dz],
                      [0, 0, 0, 1]])

        # Projection 3D -> 2D matrix
        A2 = np.array([[f, 0, w / 2, 0],
                       [0, f, h / 2, 0],
                       [0, 0, 1, 0]])

        # Final transformation matrix
        return np.dot(A2, np.dot(T, np.dot(R, A1)))


class Rectangle(object):
    def __init__(self, rect_tuple, scale=(1., 1.)):
        x, y, w, h = rect_tuple
        self.x = int(x * scale[0])
        self.y = int(y * scale[1])
        self.w = int(w * scale[0])
        self.h = int(h * scale[1])

    def scale(self, scale_x, scale_y):
        x = int(scale_x * self.x)
        w = int(scale_x * self.x)
        y = int(scale_y * self.y)
        h = int(scale_y * self.h)
        return Rectangle((x, y, w, h))

    def __repr__(self):
        return "x: %d w: %d y: %d h: %d" % (self.x, self.w, self.y, self.h)


class BodyTrackerPipeline(object):
    def __init__(self, min_x=0, min_y=0):
        self._cascade = cv2.CascadeClassifier('/usr/share/OpenCV/haarcascades/haarcascade_fullbody.xml')
        self._min_x = min_x
        self._min_y = min_y

    def process_frame(self, frame):
        rectangles = []
        for r in self._cascade.detectMultiScale(frame,
                                                1.05, 1, 0,
                                                (self._min_x, self._min_y)):
            rectangles.append(Rectangle(r))
        return rectangles


class MotionTrackerPipeline(object):
    def __init__(self, coeff_min_area=0.025, coeff_max_area=0.5):
        self.frame_initial = None
        self.coeff_min_area = coeff_min_area
        self.coeff_max_area = coeff_max_area

        self._cv_br = CvBridge()
        self._publisher_image_debug = rospy.Publisher('cv_peek', Image, queue_size=1)

    def process_frame(self, frame, phi=None, blur=True):

        if self.frame_initial is None:
            if blur:
                self.frame_initial = cv2.GaussianBlur(frame, (5, 5), 0)
            else:
                self.frame_initial = frame
            return []

        frame_initial = self.frame_initial
        if blur:
            frame_final = cv2.GaussianBlur(frame, (5, 5), 0)
        else:
            frame_final = frame

        if phi is not None:
            frame_initial_warped = np.reshape(frame_initial.copy(), (frame.shape[0], frame.shape[1], 1))

            dx = 2.646771 * (180. / np.pi) * phi

            it = ImageTransformer(frame_initial_warped)
            frame_initial_warped = it.rotate_along_axis(phi=phi, dx=dx)

            if rospy.get_param('debug/video_source') == '/pelicannon/image_transform':
                self._publisher_image_debug.publish(
                    self._cv_br.cv2_to_imgmsg(frame_initial_warped, encoding="passthrough"))

            frame_initial = frame_initial_warped

        else:
            if rospy.get_param('debug/video_source') == '/pelicannon/image_transform':
                self._publisher_image_debug.publish(
                    self._cv_br.cv2_to_imgmsg(frame_initial, encoding="passthrough"))

        frame_delta = cv2.absdiff(frame_initial, frame_final)

        if phi is not None:

            frame_x = abs(int(dx*2))
            frame_y = abs(int(dx))

            frame_delta[0:frame_y, 0:frame_delta.shape[1]] = 0
            frame_delta[frame_delta.shape[0] - frame_y:frame_delta.shape[0], 0:frame_delta.shape[1]] = 0

            frame_delta[0:frame_delta.shape[0], 0:frame_x] = 0
            frame_delta[0:frame_delta.shape[0], frame_delta.shape[1] - frame_x:frame_delta.shape[1]] = 0

        if rospy.get_param('debug/video_source') == '/pelicannon/image_abs_diff':
            self._publisher_image_debug.publish(self._cv_br.cv2_to_imgmsg(frame_delta, encoding="passthrough"))

        if phi is None:
            thresh = cv2.threshold(frame_delta, 32, 255, cv2.THRESH_BINARY)[1]
        else:
            thresh = cv2.threshold(frame_delta, 160, 255, cv2.THRESH_BINARY)[1]

        # dilate the thresholded image to fill in holes, then find contours
        # on thresholded image
        thresh = cv2.dilate(thresh, None, iterations=3)

        if rospy.get_param('debug/video_source') == '/pelicannon/image_thresh':
            self._publisher_image_debug.publish(self._cv_br.cv2_to_imgmsg(thresh, encoding="passthrough"))

        (cnts, _) = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL,
                                     cv2.CHAIN_APPROX_SIMPLE)

        rectangles = []
        for c in cnts:
            area = cv2.contourArea(c)
            if area < int(self.coeff_min_area * frame.shape[0] * frame.shape[1]) or area > int(
                    self.coeff_max_area * frame.shape[0] * frame.shape[1]):
                continue
            rectangles.append(Rectangle(cv2.boundingRect(c)))

        # Store this frame as the next frame initial
        self.frame_initial = frame_final

        #if phi <= 0.005:
        return rectangles
        return []