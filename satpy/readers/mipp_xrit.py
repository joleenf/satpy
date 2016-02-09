#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2010, 2011, 2013, 2014, 2015.

# Author(s):

#   Martin Raspaud <martin.raspaud@smhi.se>
#   Esben S. Nielsen <esn@dmi.dk>
#   Panu Lahtinen <panu.lahtinen@fmi.fi>
#   Adam Dybbroe <adam.dybbroe@smhi.se>

# This file is part of satpy.

# satpy is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.

# satpy is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU General Public License for more details.

# You should have received a copy of the GNU General Public License along with
# satpy.  If not, see <http://www.gnu.org/licenses/>.

"""Interface to Eumetcast level 1.5 HRIT/LRIT format. Uses the MIPP reader.
"""
import ConfigParser
import os
from pyproj import Proj

from mipp import xrit
from mipp import CalibrationError, ReaderError

from satpy import CONFIG_PATH
import logging
from trollsift.parser import Parser

from satpy.satin.helper_functions import area_defs_to_extent
from satpy.projectable import Projectable

LOGGER = logging.getLogger(__name__)


try:
    # Work around for on demand import of pyresample. pyresample depends
    # on scipy.spatial which memory leaks on multiple imports
    IS_PYRESAMPLE_LOADED = False
    from pyresample import geometry
    IS_PYRESAMPLE_LOADED = True
except ImportError:
    LOGGER.warning("pyresample missing. Can only work in satellite projection")

from satpy.readers import Reader


class XritReader(Reader):

    '''Class for reading XRIT data.
    '''
    pformat = "mipp_xrit"

    def __init__(self, *args, **kwargs):
        Reader.__init__(self, *args, **kwargs)

    def load(self, datasets_to_load, calibrate=True, areas=None, **kwargs):
        """Read imager data from file and return datasets.
        """
        LOGGER.debug("Channels to load: %s" % datasets_to_load)

        area_converted_to_extent = False

        pattern = self.file_patterns[0]
        parser = Parser(pattern)

        image_files = []
        prologue_file = None
        epilogue_file = None

        for filename in self.filenames:
            file_info = parser.parse(filename)
            if file_info["segment"] == "EPI":
                epilogue_file = filename
            elif file_info["segment"] == "PRO":
                prologue_file = filename
            else:
                image_files.append(filename)

        projectables = {}
        area_extent = None
        for ds in datasets_to_load:

            channel_files = []
            for filename in image_files:
                file_info = parser.parse(filename)
                if file_info["dataset_name"] == ds.name:
                    channel_files.append(filename)


            # Convert area definitions to maximal area_extent
            if not area_converted_to_extent and areas is not None:
                metadata = xrit.sat.load_files(prologue_file,
                                               channel_files,
                                               epilogue_file,
                                               only_metadata=True)
                # otherwise use the default value (MSG3 extent at
                # lon0=0.0), that is, do not pass default_extent=area_extent
                area_extent = area_defs_to_extent(areas, metadata.proj4_params)
                area_converted_to_extent = True

            try:
                image = xrit.sat.load_files(prologue_file,
                                            channel_files,
                                            epilogue_file,
                                            mask=True,
                                            calibrate=calibrate)
                if area_extent:
                    metadata, data = image(area_extent)
                else:
                    metadata, data = image()
            except CalibrationError:
                LOGGER.warning(
                    "Loading non calibrated data since calibration failed.")
                image = xrit.sat.load_files(prologue_file,
                                            channel_files,
                                            epilogue_file,
                                            mask=True,
                                            calibrate=False)
                if area_extent:
                    metadata, data = image(area_extent)
                else:
                    metadata, data = image()

            except ReaderError as err:
                # if dataset can't be found, go on with next dataset
                LOGGER.error(str(err))
                continue

            projectable = Projectable(data,
                                      name=ds.name,
                                      units=metadata.calibration_unit,
                                      wavelength_range=self.datasets[ds]["wavelength_range"],
                                      sensor=self.datasets[ds]["sensor"],
                                      start_time=self.start_time)

            # Build an area on the fly from the mipp metadata
            proj_params = getattr(metadata, "proj4_params").split(" ")
            proj_dict = {}
            for param in proj_params:
                key, val = param.split("=")
                proj_dict[key] = val

            if IS_PYRESAMPLE_LOADED:
                # Build area_def on-the-fly
                projectable.info["area"] = geometry.AreaDefinition(

                    str(metadata.area_extent) +
                    str(data.shape),
                    "On-the-fly area",
                    proj_dict["proj"],
                    proj_dict,
                    data.shape[1],
                    data.shape[0],
                    metadata.area_extent)
            else:
                LOGGER.info("Could not build area, pyresample missing...")

            projectables[ds] = projectable
        return projectables



