#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2017-2019 Satpy developers
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
"""SEVIRI native format reader.

Notes:
    When loading solar channels, this reader applies a correction for the
    Sun-Earth distance variation throughout the year - as recommended by
    the EUMETSAT document:
        'Conversion from radiances to reflectances for SEVIRI warm channels'
    In the unlikely situation that this correction is not required, it can be
    removed on a per-channel basis using the
    satpy.readers.utils.remove_earthsun_distance_correction(channel, utc_time)
    function.

References:
    - `MSG Level 1.5 Native Format File Definition`_
    - `MSG Level 1.5 Image Data Format Description`_
    - `Conversion from radiances to reflectances for SEVIRI warm channels`_

.. _MSG Level 1.5 Native Format File Definition
    https://www.eumetsat.int/website/wcm/idc/idcplg?IdcService=GET_FILE&dDocName=PDF_FG15_MSG-NATIVE-FORMAT-15&RevisionSelectionMethod=LatestReleased&Rendition=Web
.. _MSG Level 1.5 Image Data Format Description
    https://www.eumetsat.int/website/wcm/idc/idcplg?IdcService=GET_FILE&dDocName=PDF_TEN_05105_MSG_IMG_DATA&RevisionSelectionMethod=LatestReleased&Rendition=Web
.. _Conversion from radiances to reflectances for SEVIRI warm channels:
    https://www.eumetsat.int/website/wcm/idc/idcplg?IdcService=GET_FILE&dDocName=PDF_MSG_SEVIRI_RAD2REFL&
    RevisionSelectionMethod=LatestReleased&Rendition=Web

"""

import logging
from datetime import datetime
import numpy as np

import xarray as xr
import dask.array as da

from satpy import CHUNK_SIZE

from pyresample import geometry

from satpy.readers.file_handlers import BaseFileHandler
from satpy.readers.eum_base import recarray2dict
from satpy.readers.seviri_base import (SEVIRICalibrationHandler,
                                       CHANNEL_NAMES, CALIB, SATNUM,
                                       dec10216, VISIR_NUM_COLUMNS,
                                       VISIR_NUM_LINES, HRV_NUM_COLUMNS, HRV_NUM_LINES,
                                       VIS_CHANNELS, pad_data_ew, pad_data_sn)
from satpy.readers.seviri_l1b_native_hdr import (GSDTRecords, native_header,
                                                 native_trailer)
from satpy.readers._geos_area import get_area_definition

logger = logging.getLogger('native_msg')


class NativeMSGFileHandler(BaseFileHandler, SEVIRICalibrationHandler):
    """SEVIRI native format reader.

    The Level1.5 Image data calibration method can be changed by adding the
    required mode to the Scene object instantiation  kwargs eg
    kwargs = {"calib_mode": "gsics",}

    **Padding of the HRV channel**

    By default, the HRV channel is loaded padded with no-data, that is it is
    returned as a full-disk dataset. If you want the original, unpadded, data,
    just provide the `fill_hrv` as False in the `reader_kwargs`::

        scene = satpy.Scene(filenames,
                            reader='seviri_l1b_native',
                            reader_kwargs={'fill_hrv': False})
    """

    def __init__(self, filename, filename_info, filetype_info, calib_mode='nominal', fill_hrv=True):
        """Initialize the reader."""
        super(NativeMSGFileHandler, self).__init__(filename,
                                                   filename_info,
                                                   filetype_info)
        self.platform_name = None
        self.calib_mode = calib_mode
        self.fill_hrv = fill_hrv

        # Declare required variables.
        # Assume a full disk file, reset in _read_header if otherwise.
        self.header = {}
        self.mda = {}
        self.mda['is_full_disk'] = True
        self.trailer = {}

        # Read header, prepare dask-array, read trailer
        # Available channels are known only after the header has been read
        self._read_header()
        self.dask_array = da.from_array(self._get_memmap(), chunks=(CHUNK_SIZE,))
        self._read_trailer()

    @property
    def start_time(self):
        """Read the repeat cycle start time from metadata."""
        return self.header['15_DATA_HEADER']['ImageAcquisition'][
            'PlannedAcquisitionTime']['TrueRepeatCycleStart']

    @property
    def end_time(self):
        """Read the repeat cycle end time from metadata."""
        return self.header['15_DATA_HEADER']['ImageAcquisition'][
            'PlannedAcquisitionTime']['PlannedRepeatCycleEnd']

    @staticmethod
    def _calculate_area_extent(center_point, north, east, south, west,
                               we_offset, ns_offset, column_step, line_step):
        # For Earth model 2 and full disk VISIR, (center_point - west - 0.5 + we_offset) must be -1856.5 .
        # See MSG Level 1.5 Image Data Format Description Figure 7 - Alignment and numbering of the non-HRV pixels.

        ll_c = (center_point - east + 0.5 + we_offset) * column_step
        ll_l = (north - center_point + 0.5 + ns_offset) * line_step
        ur_c = (center_point - west - 0.5 + we_offset) * column_step
        ur_l = (south - center_point - 0.5 + ns_offset) * line_step

        return (ll_c, ll_l, ur_c, ur_l)

    def _get_data_dtype(self):
        """Get the dtype of the file based on the actual available channels."""
        pkhrec = [
            ('GP_PK_HEADER', GSDTRecords.gp_pk_header),
            ('GP_PK_SH1', GSDTRecords.gp_pk_sh1)
        ]
        pk_head_dtype = np.dtype(pkhrec)

        def get_lrec(cols):
            lrec = [
                ("gp_pk", pk_head_dtype),
                ("version", np.uint8),
                ("satid", np.uint16),
                ("time", (np.uint16, 5)),
                ("lineno", np.uint32),
                ("chan_id", np.uint8),
                ("acq_time", (np.uint16, 3)),
                ("line_validity", np.uint8),
                ("line_rquality", np.uint8),
                ("line_gquality", np.uint8),
                ("line_data", (np.uint8, cols))
            ]

            return lrec

        # each pixel is 10-bits -> one line of data has 25% more bytes
        # than the number of columns suggest (10/8 = 1.25)
        visir_rec = get_lrec(int(self.mda['number_of_columns'] * 1.25))
        number_of_visir_channels = len(
            [s for s in self.mda['channel_list'] if not s == 'HRV'])
        drec = [('visir', (visir_rec, number_of_visir_channels))]

        if self.mda['available_channels']['HRV']:
            hrv_rec = get_lrec(int(self.mda['hrv_number_of_columns'] * 1.25))
            drec.append(('hrv', (hrv_rec, 3)))

        return np.dtype(drec)

    def _get_memmap(self):
        """Get the memory map for the SEVIRI data."""
        with open(self.filename) as fp:
            data_dtype = self._get_data_dtype()
            hdr_size = native_header.itemsize

            return np.memmap(fp, dtype=data_dtype,
                             shape=(self.mda['number_of_lines'],),
                             offset=hdr_size, mode="r")

    def _read_header(self):
        """Read the header info."""
        data = np.fromfile(self.filename,
                           dtype=native_header, count=1)

        self.header.update(recarray2dict(data))

        data15hd = self.header['15_DATA_HEADER']
        sec15hd = self.header['15_SECONDARY_PRODUCT_HEADER']

        # Set the list of available channels:
        self.mda['available_channels'] = get_available_channels(self.header)
        self.mda['channel_list'] = [i for i in CHANNEL_NAMES.values()
                                    if self.mda['available_channels'][i]]

        self.platform_id = data15hd[
            'SatelliteStatus']['SatelliteDefinition']['SatelliteId']
        self.mda['platform_name'] = "Meteosat-" + SATNUM[self.platform_id]

        equator_radius = data15hd['GeometricProcessing'][
                             'EarthModel']['EquatorialRadius'] * 1000.
        north_polar_radius = data15hd[
                                 'GeometricProcessing']['EarthModel']['NorthPolarRadius'] * 1000.
        south_polar_radius = data15hd[
                                 'GeometricProcessing']['EarthModel']['SouthPolarRadius'] * 1000.
        polar_radius = (north_polar_radius + south_polar_radius) * 0.5
        ssp_lon = data15hd['ImageDescription'][
            'ProjectionDescription']['LongitudeOfSSP']

        self.mda['projection_parameters'] = {'a': equator_radius,
                                             'b': polar_radius,
                                             'h': 35785831.00,
                                             'ssp_longitude': ssp_lon}

        north = int(sec15hd['NorthLineSelectedRectangle']['Value'])
        east = int(sec15hd['EastColumnSelectedRectangle']['Value'])
        south = int(sec15hd['SouthLineSelectedRectangle']['Value'])
        west = int(sec15hd['WestColumnSelectedRectangle']['Value'])

        ncolumns = west - east + 1
        nrows = north - south + 1

        # check if the file has less rows or columns than
        # the maximum, if so it is an area of interest file
        if (nrows < VISIR_NUM_LINES) or (ncolumns < VISIR_NUM_COLUMNS):
            self.mda['is_full_disk'] = False

        # If the number of columns in the file is not divisible by 4,
        # UMARF will add extra columns to the file
        modulo = ncolumns % 4
        padding = 0
        if modulo > 0:
            padding = 4 - modulo
        cols_visir = ncolumns + padding

        # Check the VISIR calculated column dimension against
        # the header information
        cols_visir_hdr = int(sec15hd['NumberColumnsVISIR']['Value'])
        if cols_visir_hdr != cols_visir:
            logger.warning(
                "Number of VISIR columns from the header is incorrect!")
            logger.warning("Header: %d", cols_visir_hdr)
            logger.warning("Calculated: = %d", cols_visir)

        # HRV Channel - check if the area is reduced in east west
        # direction as this affects the number of columns in the file
        cols_hrv_hdr = int(sec15hd['NumberColumnsHRV']['Value'])
        if ncolumns < VISIR_NUM_COLUMNS:
            cols_hrv = cols_hrv_hdr
        else:
            cols_hrv = int(cols_hrv_hdr / 2)

        # self.mda represents the 16bit dimensions not 10bit
        self.mda['number_of_lines'] = int(sec15hd['NumberLinesVISIR']['Value'])
        self.mda['number_of_columns'] = cols_visir
        self.mda['hrv_number_of_lines'] = int(sec15hd["NumberLinesHRV"]['Value'])
        self.mda['hrv_number_of_columns'] = cols_hrv

    def _read_trailer(self):

        hdr_size = native_header.itemsize
        data_size = (self._get_data_dtype().itemsize *
                     self.mda['number_of_lines'])

        with open(self.filename) as fp:
            fp.seek(hdr_size + data_size)
            data = np.fromfile(fp, dtype=native_trailer, count=1)

        self.trailer.update(recarray2dict(data))

    def get_area_def(self, dataset_id):
        """Get the area definition of the band."""
        pdict = {}
        pdict['a'] = self.mda['projection_parameters']['a']
        pdict['b'] = self.mda['projection_parameters']['b']
        pdict['h'] = self.mda['projection_parameters']['h']
        pdict['ssp_lon'] = self.mda['projection_parameters']['ssp_longitude']

        if dataset_id['name'] == 'HRV':
            pdict['a_name'] = 'geos_seviri_hrv'
            pdict['p_id'] = 'seviri_hrv'

            area_extent, nlines, ncolumns, hrv_window = self.get_area_extent(dataset_id)

            area = list()
            for ae, nl, nc, win in zip(area_extent, nlines, ncolumns, hrv_window):
                pdict['a_desc'] = 'SEVIRI high resolution channel, %s window' % win
                pdict['nlines'] = nl
                pdict['ncols'] = nc
                area.append(get_area_definition(pdict, ae))

            if len(area) == 1:
                area = area[0]
            elif len(area) == 2:
                area = geometry.StackedAreaDefinition(area[0], area[1])
                area = area.squeeze()
            else:
                raise IndexError('Unexpected number of HRV windows')

        else:
            pdict['nlines'] = self.mda['number_of_lines']
            pdict['ncols'] = self.mda['number_of_columns']
            pdict['a_name'] = 'geos_seviri_visir'
            pdict['a_desc'] = 'SEVIRI low resolution channel area'
            pdict['p_id'] = 'seviri_visir'

            area = get_area_definition(pdict, self.get_area_extent(dataset_id))

        return area

    def get_area_extent(self, dataset_id):
        """Get the area extent of the file.

        Until December 2017, the data is shifted by 1.5km SSP North and West against the nominal GEOS projection. Since
        December 2017 this offset has been corrected. A flag in the data indicates if the correction has been applied.
        If no correction was applied, adjust the area extent to match the shifted data.

        For more information see Section 3.1.4.2 in the MSG Level 1.5 Image Data Format Description. The correction
        of the area extent is documented in a `developer's memo <https://github.com/pytroll/satpy/wiki/
        SEVIRI-georeferencing-offset-correction>`_.
        """
        data15hd = self.header['15_DATA_HEADER']
        sec15hd = self.header['15_SECONDARY_PRODUCT_HEADER']

        # check for Earth model as this affects the north-south and
        # west-east offsets
        # section 3.1.4.2 of MSG Level 1.5 Image Data Format Description
        earth_model = data15hd['GeometricProcessing']['EarthModel'][
            'TypeOfEarthModel']
        if earth_model == 2:
            ns_offset = 0
            we_offset = 0
        elif earth_model == 1:
            ns_offset = -0.5
            we_offset = 0.5
            if dataset_id['name'] == 'HRV':
                ns_offset = -1.5
                we_offset = 1.5
        else:
            raise NotImplementedError(
                'Unrecognised Earth model: {}'.format(earth_model)
            )

        if dataset_id['name'] == 'HRV':
            grid_origin = data15hd['ImageDescription']['ReferenceGridHRV']['GridOrigin']
            center_point = (HRV_NUM_COLUMNS / 2) - 2
            coeff = 3
            column_step = data15hd['ImageDescription']['ReferenceGridHRV']['ColumnDirGridStep'] * 1000.0
            line_step = data15hd['ImageDescription']['ReferenceGridHRV']['LineDirGridStep'] * 1000.0
        else:
            grid_origin = data15hd['ImageDescription']['ReferenceGridVIS_IR']['GridOrigin']
            center_point = VISIR_NUM_COLUMNS / 2
            coeff = 1
            column_step = data15hd['ImageDescription']['ReferenceGridVIS_IR']['ColumnDirGridStep'] * 1000.0
            line_step = data15hd['ImageDescription']['ReferenceGridVIS_IR']['LineDirGridStep'] * 1000.0

        # Calculations assume grid origin is south-east corner
        # section 7.2.4 of MSG Level 1.5 Image Data Format Description
        origins = {0: 'NW', 1: 'SW', 2: 'SE', 3: 'NE'}
        if grid_origin != 2:
            msg = 'Grid origin not supported number: {}, {} corner'.format(
                grid_origin, origins[grid_origin]
            )
            raise NotImplementedError(msg)

        # check if data is in Rapid Scanning Service mode (RSS)
        is_rapid_scan = self.trailer['15TRAILER']['ImageProductionStats']['ActualScanningSummary']['ReducedScan']

        # If we're dealing with HRV data, three different configurations of the data must be considered:
        #   1. Full Earth Scanning (FES) mode: data from two separate windows with each window having its own area
        #      extent stored in the trailer.
        #   2. Rapid Scanning Service (RSS) mode: similar to FES but only with data from the the "lower" window
        #      - typically over Europe.
        #   3. Region Of Interest (ROI) mode: data for one area subset defined by the user with its area extent stored
        #      in the secondary header.
        if dataset_id['name'] == 'HRV':
            area_extent = list()
            nlines = list()
            ncolumns = list()
            window = list()

            if not self.is_roi():
                # If we're dealing with stadnard FES or RSS HRV data we use the actual navigation parameters
                # from the trailer
                hrv_bounds = self.trailer['15TRAILER']['ImageProductionStats']['ActualL15CoverageHRV'].copy()

                for hrv_window in ['Lower', 'Upper']:
                    window_south_line = hrv_bounds['%sSouthLineActual' % hrv_window]
                    window_north_line = hrv_bounds['%sNorthLineActual' % hrv_window]
                    window_east_column = hrv_bounds['%sEastColumnActual' % hrv_window]
                    window_west_column = hrv_bounds['%sWestColumnActual' % hrv_window]

                    if window_north_line > window_south_line:  # we have some of the HRV window
                        if self.fill_hrv:
                            window_east_column = 1
                            window_west_column = HRV_NUM_COLUMNS

                            if is_rapid_scan:
                                window_south_line = 1
                                window_north_line = HRV_NUM_LINES

                        window_nlines = window_north_line - window_south_line + 1
                        window_ncolumns = window_west_column - window_east_column + 1

                        area_extent.append(self._calculate_area_extent(
                            center_point, window_north_line, window_east_column,
                            window_south_line, window_west_column,
                            we_offset, ns_offset, column_step, line_step))
                        nlines.append(window_nlines)
                        ncolumns.append(window_ncolumns)
                        window.append(hrv_window.lower())
            else:
                # If we're dealing with HRV data for a selected region of interest (ROI) we use the selected navigation
                # parameters from the secondary header information
                roi_nlines = self.mda['hrv_number_of_lines']
                roi_ncolumns = self.mda['hrv_number_of_columns']
                sec15hd = self.header['15_SECONDARY_PRODUCT_HEADER']
                roi_south_line = coeff * int(sec15hd['SouthLineSelectedRectangle']['Value']) - 2
                roi_east_column = coeff * int(sec15hd['EastColumnSelectedRectangle']['Value']) - 2

                roi_west_column = roi_east_column + roi_ncolumns - 1
                roi_north_line = roi_south_line + roi_nlines - 1

                if self.fill_hrv:
                    roi_south_line = 1
                    roi_north_line = HRV_NUM_LINES
                    roi_east_column = 1
                    roi_west_column = HRV_NUM_COLUMNS

                    roi_nlines = HRV_NUM_LINES
                    roi_ncolumns = HRV_NUM_COLUMNS

                area_extent.append(self._calculate_area_extent(
                    center_point, roi_north_line, roi_east_column,
                    roi_south_line, roi_west_column, we_offset,
                    ns_offset, column_step, line_step))
                nlines.append(roi_nlines)
                ncolumns.append(roi_ncolumns)
                window.append('roi')

            return area_extent, nlines, ncolumns, window

        # If we're dealing with VISIR data we use the selected navigation parameters from the
        # secondary header information
        else:
            north = coeff * int(sec15hd['NorthLineSelectedRectangle']['Value'])
            east = coeff * int(sec15hd['EastColumnSelectedRectangle']['Value'])
            west = coeff * int(sec15hd['WestColumnSelectedRectangle']['Value'])
            south = coeff * int(sec15hd['SouthLineSelectedRectangle']['Value'])

            area_extent = self._calculate_area_extent(
                center_point, north, east,
                south, west, we_offset,
                ns_offset, column_step, line_step
            )

        return area_extent

    def get_dataset(self, dataset_id, dataset_info):
        """Get the dataset."""
        if dataset_id['name'] not in self.mda['channel_list']:
            raise KeyError('Channel % s not available in the file' % dataset_id['name'])
        elif dataset_id['name'] not in ['HRV']:
            shape = (self.mda['number_of_lines'], self.mda['number_of_columns'])

            # Check if there is only 1 channel in the list as a change
            # is needed in the arrray assignment ie channl id is not present
            if len(self.mda['channel_list']) == 1:
                raw = self.dask_array['visir']['line_data']
            else:
                i = self.mda['channel_list'].index(dataset_id['name'])
                raw = self.dask_array['visir']['line_data'][:, i, :]

            data = dec10216(raw.flatten())
            data = data.reshape(shape)

        else:
            shape = (self.mda['hrv_number_of_lines'], self.mda['hrv_number_of_columns'])

            raw2 = self.dask_array['hrv']['line_data'][:, 2, :]
            raw1 = self.dask_array['hrv']['line_data'][:, 1, :]
            raw0 = self.dask_array['hrv']['line_data'][:, 0, :]

            shape_layer = (self.mda['number_of_lines'], self.mda['hrv_number_of_columns'])
            data2 = dec10216(raw2.flatten())
            data2 = data2.reshape(shape_layer)
            data1 = dec10216(raw1.flatten())
            data1 = data1.reshape(shape_layer)
            data0 = dec10216(raw0.flatten())
            data0 = data0.reshape(shape_layer)

            data = np.stack((data0, data1, data2), axis=1).reshape(shape)

        xarr = xr.DataArray(data, dims=['y', 'x']).where(data != 0).astype(np.float32)

        if xarr is None:
            dataset = None
        else:
            dataset = self.calibrate(xarr, dataset_id)
            if dataset_id['name'] == 'HRV' and self.fill_hrv:
                attrs = dataset.attrs
                dataset = self.pad_hrv_data(dataset)
                dataset.attrs = attrs
            dataset.attrs['units'] = dataset_info['units']
            dataset.attrs['wavelength'] = dataset_info['wavelength']
            dataset.attrs['standard_name'] = dataset_info['standard_name']
            dataset.attrs['platform_name'] = self.mda['platform_name']
            dataset.attrs['sensor'] = 'seviri'
            dataset.attrs['orbital_parameters'] = {
                'projection_longitude': self.mda['projection_parameters']['ssp_longitude'],
                'projection_latitude': 0.,
                'projection_altitude': self.mda['projection_parameters']['h']}

        return dataset

    def is_roi(self):
        """Check if data covers a selected region of interest (ROI), rather than the default FES or RSS regions."""
        is_rapid_scan = self.trailer['15TRAILER']['ImageProductionStats']['ActualScanningSummary']['ReducedScan']

        # Standard RSS data is assumed to cover the three northmost segements, thus consisting of all 3712 columns and
        # the 1392 northmost lines
        sec15hd = self.header['15_SECONDARY_PRODUCT_HEADER']
        north = int(sec15hd['NorthLineSelectedRectangle']['Value'])
        east = int(sec15hd['EastColumnSelectedRectangle']['Value'])
        south = int(sec15hd['SouthLineSelectedRectangle']['Value'])
        west = int(sec15hd['WestColumnSelectedRectangle']['Value'])
        ncolumns = west - east + 1

        is_top3segments = (ncolumns == VISIR_NUM_COLUMNS) and \
                          (north == VISIR_NUM_LINES) and \
                          (south == 5 / 8 * VISIR_NUM_LINES + 1)

        return not self.mda['is_full_disk'] and not (is_rapid_scan and is_top3segments)

    def pad_hrv_data(self, dataset):
        """Pad HRV data with empty pixels."""
        logger.debug('Padding HRV data to full disk')

        nlines = int(self.mda['hrv_number_of_lines'])
        ncols = int(self.mda['hrv_number_of_columns'])

        if not self.is_roi():
            # If we're dealing with standard FES or RSS data we use tha actual navigation parameters from the trailer
            # data in order to pad the data correctly
            hrv_bounds = self.trailer['15TRAILER']['ImageProductionStats']['ActualL15CoverageHRV']
            data_list = list()
            for hrv_window in ['Lower', 'Upper']:
                window_south_line = hrv_bounds['%sSouthLineActual' % hrv_window]
                window_north_line = hrv_bounds['%sNorthLineActual' % hrv_window]
                window_east_column = hrv_bounds['%sEastColumnActual' % hrv_window]
                window_west_column = hrv_bounds['%sWestColumnActual' % hrv_window]

                if window_north_line > window_south_line:  # we have some of the HRV window
                    window_nlines = window_north_line - window_south_line + 1
                    window_ncols = window_west_column - window_east_column + 1
                    line_start = window_south_line - HRV_NUM_LINES + nlines
                    line_end = line_start + window_nlines - 1

                    # Pad data in east-west direction
                    data_window = pad_data_ew(dataset[line_start - 1:line_end, :].data,
                                              (window_nlines, HRV_NUM_COLUMNS),
                                              window_east_column,
                                              window_east_column + window_ncols - 1)

                    data_list.append(data_window)

            if not self.mda['is_full_disk']:
                # If we are dealing with RSS data we need to pad data in north-south direction as well
                data_list = [pad_data_sn(data_list[0],
                                         (HRV_NUM_LINES, HRV_NUM_COLUMNS),
                                         hrv_bounds['LowerSouthLineActual'],
                                         hrv_bounds['LowerNorthLineActual'])]

        else:
            # If we're dealing with data for a selected region of interest we use the selected navigation parameters
            # from the secondary header information in order to pad the data correctly
            sec15hd = self.header['15_SECONDARY_PRODUCT_HEADER']
            roi_south_line = 3 * int(sec15hd['SouthLineSelectedRectangle']['Value']) - 2
            roi_east_column = 3 * int(sec15hd['EastColumnSelectedRectangle']['Value']) - 2

            # Pad data in east-west direction
            data_roi = pad_data_ew(dataset.data,
                                   (nlines, HRV_NUM_COLUMNS),
                                   roi_east_column,
                                   roi_east_column + ncols - 1)

            # Pad data in south-north direction
            data_list = [pad_data_sn(data_roi,
                                     (HRV_NUM_LINES, HRV_NUM_COLUMNS),
                                     roi_south_line,
                                     roi_south_line + nlines - 1)]

        return xr.DataArray(da.vstack(data_list), dims=('y', 'x'))

    def calibrate(self, data, dataset_id):
        """Calibrate the data."""
        tic = datetime.now()

        data15hdr = self.header['15_DATA_HEADER']
        calibration = dataset_id['calibration']
        channel = dataset_id['name']

        # even though all the channels may not be present in the file,
        # the header does have calibration coefficients for all the channels
        # hence, this channel index needs to refer to full channel list
        i = list(CHANNEL_NAMES.values()).index(channel)

        if calibration == 'counts':
            return data

        if calibration in ['radiance', 'reflectance', 'brightness_temperature']:
            # determine the required calibration coefficients to use
            # for the Level 1.5 Header
            if (self.calib_mode.upper() != 'GSICS' and self.calib_mode.upper() != 'NOMINAL'):
                raise NotImplementedError(
                    'Unknown Calibration mode : Please check')

            # NB GSICS doesn't have calibration coeffs for VIS channels
            if (self.calib_mode.upper() != 'GSICS' or channel in VIS_CHANNELS):
                coeffs = data15hdr[
                    'RadiometricProcessing']['Level15ImageCalibration']
                gain = coeffs['CalSlope'][i]
                offset = coeffs['CalOffset'][i]
            else:
                coeffs = data15hdr[
                    'RadiometricProcessing']['MPEFCalFeedback']
                gain = coeffs['GSICSCalCoeff'][i]
                offset = coeffs['GSICSOffsetCount'][i]
                offset = offset * gain
            res = self._convert_to_radiance(data, gain, offset)

        if calibration == 'reflectance':
            solar_irradiance = CALIB[self.platform_id][channel]["F"]
            res = self._vis_calibrate(res, solar_irradiance)

        elif calibration == 'brightness_temperature':
            cal_type = data15hdr['ImageDescription'][
                'Level15ImageProduction']['PlannedChanProcessing'][i]
            res = self._ir_calibrate(res, channel, cal_type)

        logger.debug("Calibration time " + str(datetime.now() - tic))
        return res


def get_available_channels(header):
    """Get the available channels from the header information."""
    chlist_str = header['15_SECONDARY_PRODUCT_HEADER'][
        'SelectedBandIDs']['Value']
    retv = {}

    for idx, char in zip(range(12), chlist_str):
        retv[CHANNEL_NAMES[idx + 1]] = (char == 'X')

    return retv
