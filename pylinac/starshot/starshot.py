# -*- coding: utf-8 -*-
"""
Created on Mon Dec 02 00:19:16 2013

@author: James
"""
from __future__ import division, print_function
from future import standard_library
standard_library.install_hooks()
from future.builtins import range
import os.path as osp

import numpy as np
from scipy.ndimage.interpolation import map_coordinates
from scipy.ndimage.filters import median_filter
from matplotlib.patches import Circle
import matplotlib.pyplot as plt

from pylinac.common.common_functions import Prof_Penum, point2edge_min, point_to_2point_line_dist
from pylinac.common.image_classes import SingleImageObject
from pylinac.common.peakdetect import peak_detect


""" Default constants """
# tolerance and pixel scale values. The algo does a neighbor search, stopping when the tolerance is met.
# The scale is the pixel size to search, e.g. scale=1 searches to the nearest whole pixel, while scale=10 searches to
# the nearest 1/10th of a pixel, etc.
normal_tolerance, normal_scale = 0.05, 1.0
small_tolerance, small_scale = 0.0001, 10.0

file_dir = osp.split(osp.abspath(__file__))[0]  # The working directory of this file

class Starshot(SingleImageObject):
    """Creates a Starshot instance for determining the wobble in a gantry, collimator,
    couch or MLC starshot image pattern.
    """
    def __init__(self):
        SingleImageObject.__init__(self)
        self._mechpoint = None  # (y,x) which specifies the mechanical isocenter & starting point for search algorithm
        self.radius = 50  # default of 50% of smallest image dimension
        self._pointpairs = []  # a list which holds 4 values per index: two points with the y,x locations of points that
        # correspond to the two points comprising a radiation "strip"
        self._circleprofile = None  # a numpy array that will hold a 1-D profile of a circle that surrounds the mechanical isocenter
        self._x = None  # an array that holds the x-values that the circleprofile is computed over
        self._y = None  # ditto for y-values
        self._wobble_center = None  # The pixel position (y,x) of the center of a circle that minimally touches all the radiation lines
        self._wobble_radius = None  # The radius of the circle mentioned above. Could be in pixels or mm
        self._wobble_radius_pix = None  # The radius of the circle in pixels. For proper drawing of the circle on the plot.
        self.tolerance = 1  # tolerance limit of the radiation wobble
        self.tolerance_unit = 'pixels'  # tolerance units are initially pixels. Will be converted to mm if conversion
        # information available in image properties
        self.wobble_passed = False  # overall test result

    def load_demo_image(self, number=1):
        """Load a starshot demo image.

        :param number: There are a few demo images. This number will choose which demo file to use. As of now
            there are 2 demo images.
        :type number: int

        """
        if number == 1:
            im_open_path = osp.join(file_dir, "demo files", "demo_starshot_2.tif")
        else:
            im_open_path = osp.join(file_dir, "demo files", "demo_starshot_1.tif")
        self.load_image(im_open_path)

    def set_mech_point(self, point, warn_if_far_away=True):
        """Set the mechanical isocenter (i.e. starting point) point manually.

        :param point: [y,x]
        :type point: list
        :param warn_if_far_away: If the point is far away from the automatic determination, warn user
        :type warn_if_far_away: boolean
        """
        if warn_if_far_away:
            if self._mechpoint is None:
                self._auto_set_mech_point()
            tolerance = max(min(self.image.shape)/100, 15)  # 1% image width of smalling dimension, or 15 pixels
            auto_y_upper = self._mechpoint[0] - tolerance
            auto_y_lower = self._mechpoint[0] + tolerance
            auto_x_left = self._mechpoint[1] - tolerance
            auto_x_right = self._mechpoint[1] + tolerance
            if (point[0] < auto_y_upper or point[0] > auto_y_lower) \
                or (point[1] < auto_x_left or point[1] > auto_x_right):
                print("Warning: The point you've set is far away from the automatic calculation.\n" +
                      " The algorithm may not calculate correctly if you continue. \nUse method .clear_mech_point" +
                      " to reset if need be or don't set the mech point manually.")

        self._mechpoint = point

    def clear_mech_point(self):
        """Clear/reset the mechanical iso."""
        self._mechpoint = None

    def _draw_profile_circle(self, im_widget):
        """Draw a circle where the circular profile was or will be taken over.
        :param im_widget: The widget to draw to profile to.
        :type im_widget: matplotlib.Figure
        """
        mindist = point2edge_min(self.image, self._mechpoint)
        center = self._mechpoint
        radius = self.radius/100 * mindist
        # x0, y0, x1, y1 = wc[1] - wr, wc[0] - wr, wc[1] + wr, wc[0] + wr
        im_widget.axes.add_patch(Circle(center, radius=radius))
        im_widget.draw()

    def _check_inversion(self, allow_inversion=True):
        """Check the image for proper inversion (pixel value increases with dose).

        Inversion is checked by the following:
        - Summing the image along both horizontal and vertical directions.
        - If the maximum point of both horizontal and vertical is in the middle 1/3, the image is assumed to be correct.
        - Otherwise, invert the image.

        See .analyze() for parameter descriptions.
        """
        if not allow_inversion:
            return

        # sum the image along each axis
        x_sum = np.sum(self.image, 0)
        y_sum = np.sum(self.image, 1)

        # determine the point of max value for each sum profile
        xmaxind = np.argmax(x_sum)
        ymaxind = np.argmax(y_sum)

        # If that maximum point isn't near the center (central 1/3), invert image.
        if ((xmaxind > len(x_sum) / 3 and xmaxind < len(x_sum) * 2 / 3) and
                (ymaxind > len(y_sum) / 3 and ymaxind < len(y_sum) * 2 / 3)):
            pass
        else:
            self.invert_image()

    def _auto_set_mech_point(self):
        """Set the mechanical iso point automatically.

        The determination of an automatic mech point is accomplished by finding the Full-Width-80%-Max.
        Finding the maximum pixel does not consistently work, esp. in the presence of a pin prick. The
        FW80M is a more consistent metric for finding a good start point.
        """

        # sum the image along each axis
        x_prof = np.sum(self.image, 0)
        y_prof = np.sum(self.image, 1)

        # Calculate Full-Width, 80% Maximum
        x_point = Prof_Penum(x_prof).get_FWXM_center(80)
        y_point = Prof_Penum(y_prof).get_FWXM_center(80)

        self.set_mech_point([y_point, x_point], warn_if_far_away=False)

    def analyze(self, allow_inversion=True, radius=50, min_peak_height=30):
        """Analyze the starshot image.
         Analyze finds the minimum radius and center of a circle that touches all the lines
         (i.e. the wobble circle diameter and wobble center)

         :param allow_inversion: Specifies whether to let the algorithm automatically check the image for proper inversion. Analysis will
            likely fail without proper inversion. Use .invert_image() to manually invert.
         :type allow_inversion: boolean
         :param radius: Distance in % between starting isocenter (mech point) and closest image edge.
         :type radius: int, float
         :param min_peak_height: The percentage minimum height a peak must be to be considered a valid peak. A lower value catches
            radiation peaks that vary in magnitude (e.g. different MU delivered), but also could pick up noise. Raise if pixel values of
            strips are similar but noise is getting caught. Also try changing radius if noise is a problem.
         :type min_peak_height: int

        """
        if self.image is None:
            raise AttributeError("Starshot image not yet loaded")
        if type(radius) != float and type(radius) != int:
            raise TypeError("Radius must be an int or float")
        if radius < 5 or radius > 95:
            raise ValueError("Radius must be between 5 and 95")
        if type(min_peak_height) != int:
            raise TypeError("Peak height must be an integer")
        elif min_peak_height < 5 or min_peak_height > 95:
            raise ValueError("Peak height must be between 5 and 95")

        # check inversion
        self._check_inversion(allow_inversion)

        # set starting point automatically if not yet set
        if self._mechpoint is None:
            self._auto_set_mech_point()

        # extract the circle profile
        self._get_circle_profile(radius)
        # determine the peaks of the profile
        self._find_peaks(min_peak_height)
        # match peaks that are from the same radiation strip
        self._match_peaks()
        # find the wobble
        self._find_wobble_2step()
        # check if results pass tolerance
        self._check_if_passed()

    def _get_circle_profile(self, radius):
        """Extracts values of a circular profile around the isocenter point atop the image matrix,
        later to be searched for peaks and such. See .analyze() for parameter definitions.
        """

        # find smallest pixel distance from mechanical point to image edge
        mindist = point2edge_min(self.image, self._mechpoint)

        # create index and cos, sin points which will be the circle's rectilinear coordinates
        deg = np.arange(0, 360 - 0.01, 0.01)
        x = np.cos(np.deg2rad(deg)) * radius / 100 * mindist + self._mechpoint[1]
        y = np.sin(np.deg2rad(deg)) * radius / 100 * mindist + self._mechpoint[0]

        # this scipy function pulls the values of the image along the y,x points defined above
        raw_prof = map_coordinates(self.image, [y, x], order=0)
        filt_prof = median_filter(raw_prof, size=100)  # filter the profile
        norm_prof = filt_prof - np.min(filt_prof)  # normalize the new profile

        # Roll the profile if needed
        # --------------------------
        # In order to properly find the peaks, the bounds of the circular profile must not be near a radiation strip.
        # If the profile's edge (0-index) is in the middle of a radiation strip, move it over so that it's not
        zero_ind = np.where(norm_prof == 0)
        prof = np.roll(norm_prof, -zero_ind[0][0])
        x = np.roll(x, -zero_ind[0][0])
        y = np.roll(y, -zero_ind[0][0])

        self._circleprofile = prof
        self._x = x
        self._y = y
        self._profile_radius = radius/100 * mindist

    def _plot_circleprofile(self):
        """Plot the circle profile that was extracted. Helpful when debugging."""
        assert self._circleprofile is not None, "The circleprofile has not yet been computed; use get_cirlce_profile()"
        plt.plot(self._circleprofile)
        plt.show()

    def _find_peaks(self, min_peak_height):
        """Find the positions of peaks in the circle profile and map them to the starshot image."""

        # Find the positions of the max values
        # min_peak_height = np.percentile(self._circleprofile,30)  # 30% minimum peak height
        min_peak_height = (min_peak_height/100) *(np.max(self._circleprofile) - np.min(self._circleprofile))
        min_peak_distance = len(self._circleprofile)/100*3  # 3-degree minimum distance
        max_vals, max_idxs = peak_detect(self._circleprofile, threshold=min_peak_height, min_peak_distance=min_peak_distance)
        # ensure the # of peaks found was even; every radiation "strip" should result in two peaks, one on either side of the isocenter.
        if len(max_vals) % 2 != 0 or len(max_vals) == 0:
            raise Exception("The algorithm found zero or an uneven number of radiation peaks. Ensure that the mechanical " \
                                 "iso is correct and/or change the search radius. Sorry")

        # create a zero-array called strip_limits that holds the indices of the minimum between peaks.
        # In this way, we search the full-width half-max within the indices between any two indices of strip_limits
        # The first index of strip_limits is always 0 and the last is always 36,000 (or whatever the length of
        # self._circleprofile is).
        strip_limits = np.zeros(len(max_vals) + 1).astype(int)
        for i in np.arange(len(max_vals) - 1):
            strip_limits[i + 1] = (max_idxs[i + 1] - max_idxs[i]) / 2 + max_idxs[i]
        strip_limits[-1] = len(self._circleprofile)

        # Now, create and fill an array called center_indices that will be the index of _circleprofile that the FWHM is at.
        center_indices = np.zeros(len(max_vals))
        # Determine the FWHM of each peak
        for i in range(len(max_vals)):
            prof = Prof_Penum(self._circleprofile[strip_limits[i]:strip_limits[i + 1]], np.arange(strip_limits[i], strip_limits[i + 1]))
            center_indices[i] = prof.get_FWXM_center()
        center_indices = np.round(center_indices) # round to the nearest pixel
        center_indices = center_indices.astype(int) # convert to an int array

        # _peak_locs are the (y,x) position on the actual starshot image of the FWHM centers.
        self._peak_locs = np.array([self._y[center_indices], self._x[center_indices]]).T

    def _match_peaks(self):
        """
        Match the peaks found in find_peaks to the same radiation lines. E.g. if we have 12 peaks, then we have
        6 radiation "strips". There are a number of ways to match them:
            -We could, based on the starting point, calculate the "expected" location of the opposite peak and
             locate it within a tolerance.
            -Similarly, we could search the circle profile near 180 degrees from a peak to search for another peak,
             presumably the opposite peak.
            -We could simply connect the existing peaks based on an offset of peaks

        The third argument actually turns out to not only be the quickest, but also the most robust. The first two
        methods are based on a starting point. If the starting point isn't near the actual center, then the calculation
        of expected locations will be off. The third method is robust to starting points very far away from the real
        center.
        """
        # On the assumption that strips go all the way across the CAX and that we have caught them all,
        # it is easiest and most robust to simply connect index i with len(points)/2 + i, etc.
        for strip in range(len(self._peak_locs)//2):
            self._pointpairs.append(np.array([self._peak_locs[strip], self._peak_locs[strip+len(self._peak_locs)/2]]))

    def _find_wobble_2step(self):
        """
        Find the smallest radius ("wobble") and center of a circle that touches all the star lines.
        This is accomplished by two rounds of searching. The first round finds the radius and center down to
            the nearest pixel.
        The second round finds the center and radius down to sub-pixel precision using parameter scale.
        This methodology is faster than one round of searching at sub-pixel precision.
        """
        sp = self._mechpoint  # set the initial starting point from user-defined point

        # first round of searching; this finds the circle to the nearest pixel
        __, wob_cent = self._find_wobble(normal_tolerance, sp, normal_scale)
        # second round of searching; this finds the circle down to sub-pixel precision
        self._wobble_radius, self._wobble_center = self._find_wobble(small_tolerance, wob_cent, small_scale)
        # convert wobble to mm if possible
        self._wobble_radius_pix = self._wobble_radius
        if self.im_props['DPmm'] != 0:
            self.tolerance_unit = 'mm'
            self._wobble_radius /= self.im_props['DPmm']

    def _find_wobble(self, tolerance, start_point, scale):
        """
        An iterative method that moves pixel by pixel to the point of minimum distance to all radiation lines

        :param tolerance: The value the "outside" pixels must be within compared to the center pixel to stop the algorithm
        :type tolerance: float
        :param start_point: The starting point for the search algorithm.
        :type start_point: tuple
        :param scale: The scale of the search in pixels. E.g. 0.1 searches to 0.1 pixel precision.
        :type scale: float, int
        """
        sp = start_point
        #init conditions; initialize a 3x3 "ones" matrix and make corner value 0 to start minimum distance search
        distmax = np.ones((3, 3))
        distmax[0, 0] = 0

        #find min point within the given tolerance
        while np.any(distmax < distmax[1, 1] - tolerance):  # while any edge pixel value + tolerance is less than the center one...
            #find which pixel that is lower than center pixel
            min_idx = np.unravel_index(distmax.argmin(),distmax.shape)
            #set new starting point to min dist index point
            sp[0] += (min_idx[0] - 1)/scale
            sp[1] += (min_idx[1] - 1)/scale
            for x in np.arange(-1,2):
                for y in np.arange(-1,2):
                    point = np.array([sp[0] + y / scale, sp[1] + x / scale])
                    # distmax[y + 1, x + 1] = geoPointSegsDist(point, self._pointpairs, minormax='max')
                    distmax[y+1, x+1] = np.max([point_to_2point_line_dist(point, line) for line in self._pointpairs])

        wobbleradius = distmax[1, 1]
        wobblecenter = np.asarray(sp)
        return wobbleradius, wobblecenter

    def _check_if_passed(self):
        """After analysis, check that the radiation wobble passed tolerance."""
        if self._wobble_radius * 2 < self.tolerance:
            self.wobble_passed = True

    def return_string_results(self):
        """Print the results of the analysis.
        :return string: A string with a statement of the minimum circle.
        """
        if self.wobble_passed:
            passfailstr = 'PASS'
        else:
            passfailstr = 'FAIL'

        string = 'Result: %s \nThe miminum circle that touches all the star lines has a radius of %g %s. \nThe center of the minimum circle is at %f, %f' % (passfailstr, self._wobble_radius, self.tolerance_unit, self._wobble_center[0], self._wobble_center[1])
        return string

    def _plot_wobble_circle(self, im_widget):
        """
        Plot the radiation wobble circle
        :param im_widget: The axes to plot the circle on.
        :type im_widget: matplotlib.axes.Axes
        """
        # rename
        wc = self._wobble_center
        wr = self._wobble_radius

        im_widget.axes.add_patch(Circle(wc, radius=wr))
        im_widget.draw()

    def plot_analyzed_image(self, plot=None):
        """Draw the star lines, profile circle, and wobble circle on a matplotlib figure.

        :param plot: The plot to draw on. If None, will create a new one.
        :type plot: matplotlib.image.AxesImage
        """
        # plot image
        if plot is None:
            imgplot = plt.imshow(self.image)
        else:
            plot.axes.imshow(self.image)
            # plot.figure.hold(True)
            plot.axes.hold(True)
            imgplot = plot
        # plot radiation lines
        for pair in self._pointpairs:
            imgplot.axes.plot([pair[0, 1], pair[1, 1]], [pair[0, 0], pair[1, 0]], 'w')
        # plot wobble circle
        wc = np.flipud(self._wobble_center)
        wr = self._wobble_radius_pix
        imgplot.axes.add_patch(Circle(wc, radius=wr,edgecolor='black',fill=False))
        # plot profile circle
        rad = self.radius / 100.0 * point2edge_min(self.image, self._mechpoint)
        imgplot.axes.add_patch(Circle(np.flipud(self._mechpoint), radius=rad, edgecolor='green', fill=False))
        # tighten plot around image
        imgplot.axes.autoscale(tight=True)

        # Finally, show it all
        if plot is None:
            plt.show()
        else:
            plot.draw()
            plot.axes.hold(False)

    def run_demo(self):
        """Run the Starshot module demo."""
        self.load_demo_image()
        self.analyze()
        print(self.return_string_results())
        self.plot_analyzed_image()


# ----------------------------
# Starshot demo
# ----------------------------
if __name__ == '__main__':
    Starshot().run_demo()