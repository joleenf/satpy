#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2020 Satpy developers
#
# This file is part of satpy.
#
# satpy is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# satpy is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# satpy.  If not, see <http://www.gnu.org/licenses/>.
"""Advanced Himawari Imager (AHI) gridded format data reader.

This data comes in a flat binary format on a fixed grid, and needs to have
calibration coefficients applied to it in order to retrieve reflectance or BT.
LUTs can be downloaded at: ftp://hmwr829gr.cr.chiba-u.ac.jp/gridded/FD/support/

This data is gridded from the original Himawari geometry. To our knowledge,
only full disk grids are available, not for the Meso or Japan rapid scans.

References:
 - AHI gridded data website:
        http://www.cr.chiba-u.jp/databases/GEO/H8_9/FD/index_jp.html


"""

import logging
from datetime import datetime

import numpy as np
import dask.array as da
import xarray as xr
import os

from appdirs import AppDirs
from satpy import CHUNK_SIZE
from pyresample import geometry
from satpy.readers.file_handlers import BaseFileHandler
from satpy.readers.utils import unzip_file

# Hardcoded address of the reflectance and BT look-up tables
AHI_REMOTE_LUTS = ['hmwr829gr.cr.chiba-u.ac.jp',
                   '/gridded/FD/support/',
                   'count2tbb_v101.tgz']

AHI_FULLDISK_SIZES = {0.005: {'x_size': 24000,
                              'y_size': 24000},
                      0.01: {'x_size': 12000,
                             'y_size': 12000},
                      0.02: {'x_size': 6000,
                             'y_size': 6000}}

AHI_FULLDISK_EXTENT = [85., -60., 205., 60.]

AHI_CHANNEL_RES = {'vis': 0.01,
                   'ext': 0.005,
                   'sir': 0.02,
                   'tir': 0.02}

logger = logging.getLogger('ahi_grid')


class AHIGriddedFileHandler(BaseFileHandler):
    """AHI gridded format reader.

    This data is flat binary, big endian unsigned short.
    It covers the region 85E -> 205E, 60N -> 60S at variable resolution:
    - 0.005 degrees for Band 3
    - 0.01 degrees for Bands 1, 2 and 4
    - 0.02 degrees for all other bands.
    These are approximately equivalent to 0.5, 1 and 2km.

    Files can either be zipped with bz2 compression (like the HSD format
    data), or can be uncompressed flat binary.
    """

    def __init__(self, filename, filename_info, filetype_info):
        """Initialize the reader."""
        super(AHIGriddedFileHandler, self).__init__(filename, filename_info,
                                                    filetype_info)
        self.is_zipped = False
        self._unzipped = unzip_file(self.filename)
        # Assume file is not zipped
        if self._unzipped:
            # But if it is, set the filename to point to unzipped temp file
            self.is_zipped = True
            self.filename = self._unzipped
        # Get the band name, needed for finding area and dimensions
        self.product_name = filetype_info['file_type']
        self.areaname = filename_info['area']
        self.res = AHI_CHANNEL_RES[self.product_name[:3]]
        if self.areaname == 'fld':
            self.nlines = AHI_FULLDISK_SIZES[self.res]['y_size']
            self.ncols = AHI_FULLDISK_SIZES[self.res]['x_size']

        # Set up directory path for the LUTs
        app_dirs = AppDirs('ahi_gridded_luts', 'satpy', '1.0.1')
        self.lut_dir = os.path.expanduser(app_dirs.user_data_dir) + '/'

        self.sensor = 'ahi'
        self.lons = None
        self.lats = None

    def __del__(self):
        """Delete the object."""
        if (self.is_zipped and os.path.exists(self.filename)):
            os.remove(self.filename)

    def _calibrate(self, data):
        """Load calibration from LUT and apply."""

        # First, check that the LUT is available. If not, download it.
        lut_file = self.lut_dir + self.product_name
        if not os.path.exists(lut_file):
            self._download_luts()
        try:
            # Load file, it has 2 columns: DN + Refl/BT. We only need latter.
            lut = np.loadtxt(lut_file)[:, 1]
        except FileNotFoundError:
            raise FileNotFoundError("No LUT file found:", lut_file)

        # LUT may truncate NaN values, so manually set those in data
        lut_len = len(lut)
        data = np.where(data < lut_len - 1, data, np.nan)
        return lut[data.astype(np.uint16)]

    def _download_luts(self):
        """LUTs are needed for count->REFL/BT conversion. Download them."""
        from ftplib import FTP
        import tempfile
        import shutil
        import tarfile
        import pathlib

        # Check that the LUT directory exists
        pathlib.Path(self.lut_dir).mkdir(parents=True, exist_ok=True)

        # There is one LUT for each channel
        flist = ['ext.01', 'vis.01', 'vis.02', 'vis.03',
                 'sir.01', 'sir.02', 'tir.01', 'tir.02',
                 'tir.03', 'tir.04', 'tir.05', 'tir.06',
                 'tir.07', 'tir.08', 'tir.09', 'tir.10']

        # Create a temporary directory for the LUT download
        tdir = tempfile.gettempdir()
        fname = tdir + 'tmp.tgz'
        logger.info("Download AHI LUTs files and store in directory %s",
                    self.lut_dir)

        # Set up an FTP connection (anonymous) and download
        ftp = FTP(AHI_REMOTE_LUTS[0])
        ftp.login('anonymous', 'anonymous')
        ftp.cwd(AHI_REMOTE_LUTS[1])
        ftp.retrbinary("RETR " + AHI_REMOTE_LUTS[2], open(fname, 'wb').write)

        # The file is tarred, here we untar and then remove the downloaded file
        tar = tarfile.open(fname)
        tar.extractall(tdir)
        tar.close()
        os.remove(fname)
        # Loop over the LUTs and copy to the correct location
        for tf in flist:
            shutil.move(tdir + '/count2tbb/' + tf, self.lut_dir + tf)
        shutil.rmtree(tdir + '/count2tbb/')

    def get_dataset(self, key, info):
        """Get the dataset."""
        return self.read_band(key, info)

    def get_area_def(self, dsid):
        """Get the area definition.

        This is fixed, but not defined in the file. So we must
        generate it ourselves with some assumptions."""

        if self.areaname == 'fld':
            area_extent = AHI_FULLDISK_EXTENT

        proj_param = 'EPSG:4326'

        area = geometry.AreaDefinition('gridded_himawari',
                                       'A gridded Himawari area',
                                       'longlat',
                                       proj_param,
                                       self.ncols,
                                       self.nlines,
                                       area_extent)
        self.area = area

        return area

    def read_band(self, key, info):
        """Read the data."""
        tic = datetime.now()

        with open(self.filename, "rb") as fp_:
            res = da.from_array(np.memmap(self.filename,
                                          offset=fp_.tell(),
                                          dtype='>u2',
                                          shape=(self.nlines, self.ncols),
                                          mode='r'),
                                chunks=CHUNK_SIZE)
        logger.debug("Reading time " + str(datetime.now() - tic))

        # Calibrate
        res = self.calibrate(res, key['calibration'])

        # Update metadata
        new_info = dict(
            units=info['units'],
            standard_name=info['standard_name'],
            wavelength=info['wavelength'],
            resolution='resolution',
            id=key,
            name=key['name'],
            sensor=self.sensor,
        )
        res = xr.DataArray(res, attrs=new_info, dims=['y', 'x'])
        return res

    def calibrate(self, data, calib):
        """Calibrate the data."""
        tic = datetime.now()
        if calib == 'counts':
            return data
        elif calib == 'reflectance' or calib == 'brightness_temperature':
            data = self._calibrate(data)

        logger.debug("Calibration time " + str(datetime.now() - tic))
        return data
