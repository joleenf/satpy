#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2019 Satpy developers
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
"""Interface to MIRS product."""

from satpy.readers.netcdf_utils import NetCDF4FileHandler
import numpy as np
import datetime
import netCDF4
import os
import logging

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

try:
    # try getting setuptools/distribute's version of resource retrieval first
    from pkg_resources import resource_string as get_resource_string
except ImportError:
    from pkgutil import get_data as get_resource_string

# 'Polo' variable in MiRS files use these values for H/V polarization
POLO_V = 2
POLO_H = 3

# number of channels
# n_channels = 22
# number of fields of view
n_fov = 96

amsu = "amsu-mhs"
PLATFORMS = {"n18": "NOAA-18",
             "n19": "NOAA-19",
             "m02": "MetOp-A",
             "m01": "MetOp-B",
             "npp": "NPP",
             "f17": "DMSP-F17",
             "f18": "DMSP-F18",
             "gpm": "GPM",
             "n20": "NOAA-20",
             "m2": "MIRS",
             }
SENSOR = {"n18": amsu,
          "n19": amsu,
          "m01": amsu,
          "m02": amsu,
          "npp": "atms",
          "jpss": "atms",
          "f17": "ssmis",
          "f18": "ssmis",
          "gpm": "GPI",
          "n20": amsu,
          }


class MIRSHandler(NetCDF4FileHandler):
    """MIRS handler for NetCDF4 files."""

    def __init__(self, filename, filename_info, filetype_info,
                 auto_maskandscale=False, xarray_kwargs=None,
                 cache_var_size=0, cache_handle=False):
        """Init method."""
        super().__init__(filename, filename_info, filetype_info,
                         auto_maskandscale, xarray_kwargs,
                         cache_var_size, cache_handle)

    @property
    def platform_shortname(self):
        """Get platform shortname."""
        return self.filename_info['platform_shortname']

    @property
    def platform_name(self):
        """Get platform name."""
        try:
            res = PLATFORMS[self.filename_info['platform_shortname']]
        except KeyError:
            return "mirs"
        return res.lower()

    @property
    def sensor(self):
        """Get sensor."""
        try:
            res = SENSOR[self.filename_info["platform_shortname"]]
        except KeyError:
            return self.sensor_names
        return res

    @property
    def sensor_names(self):
        """Return standard sensor names for the file's data."""
        return list(set(SENSOR.values()))

    @property
    def start_time(self):
        """Get start time."""
        # old file format
        if self.filename_info.get("date", False):
            return datetime.datetime.combine(
                self.force_date("date"), self.force_time("start_time"))
        return self.filename_info["start_time"]

    @property
    def end_time(self):
        """Get end time."""
        # old file format
        if self.filename_info.get("date", False):
            return datetime.datetime.combine(
                self.force_date("date"), self.force_time("end_time"))
        return self.filename_info["end_time"]

    def force_date(self, key):
        """Force datetime.date for combine."""
        if isinstance(self.filename_info[key], datetime.datetime):
            return self.filename_info[key].date()
        else:
            return self.filename_info[key]

    def force_time(self, key):
        """Force datetime.time for combine."""
        if isinstance(self.filename_info.get(key), datetime.datetime):
            return self.filename_info.get(key).time()
        else:
            return self.filename_info.get(key)

    def _read_atms_limb_correction_coefficients(self, fn):
        if os.path.isfile(fn):
            coeff_str = open(fn, "r").readlines()
        else:
            parts = fn.split(":")
            mod_part, file_part = parts if len(parts) == 2 else ("", parts[0])
            mod_part = mod_part or __package__  # self.__module__
            coeff_str = get_resource_string(mod_part, file_part).decode().split("\n")
        # make it a generator
        coeff_str = (line.strip() for line in coeff_str)

        all_coeffs = np.zeros((22, n_fov, 22), dtype=np.float32)
        all_amean = np.zeros((22, n_fov, 22), dtype=np.float32)
        all_dmean = np.zeros(22, dtype=np.float32)
        all_nchx = np.zeros(22, dtype=np.int32)
        all_nchanx = np.zeros((22, 22), dtype=np.int32)
        all_nchanx[:] = 9999
        # There should be 22 sections
        for chan_idx in range(22):
            # blank line at the start of each section
            _ = next(coeff_str)

            # section header
            nx, nchx, dmean = [x.strip() for x in next(coeff_str).split(" ") if x]
            nx = int(nx)
            all_nchx[chan_idx] = nchx = int(nchx)
            all_dmean[chan_idx] = float(dmean)

            # coeff locations (indexes to put the future coefficients in)
            locations = [int(x.strip()) for x in next(coeff_str).split(" ") if x]
            assert (len(locations) == nchx)
            for x in range(nchx):
                all_nchanx[chan_idx, x] = locations[x] - 1

            # Read 'nchx' coefficients for each of 96 FOV (n_fov).
            for fov_idx in range(n_fov):
                # chan_num, fov_num, *coefficients, error
                coeff_line_parts = [x.strip() for x in next(coeff_str).split(" ") if x][2:]
                coeffs = [float(x) for x in coeff_line_parts[:nchx]]
                ameans = [float(x) for x in coeff_line_parts[nchx:-1]]
                # error_val = float(coeff_line_parts[-1])
                for x in range(nchx):
                    all_coeffs[chan_idx, fov_idx, all_nchanx[chan_idx, x]] = coeffs[x]
                    all_amean[all_nchanx[chan_idx, x], fov_idx, chan_idx] = ameans[x]

        return all_dmean, all_coeffs, all_amean, all_nchx, all_nchanx

    def _apply_atms_limb_correction(self, datasets, dmean, coeffs, amean, nchx, nchanx):
        all_new_ds = []
        coeff_sum = np.zeros(datasets.shape[1], dtype=datasets[0].dtype)
        for channel_idx in range(datasets.shape[0]):
            ds = datasets[channel_idx]
            new_ds = ds.copy()
            all_new_ds.append(new_ds)
            for fov_idx in range(n_fov):
                coeff_sum[:] = 0
                for k in range(nchx[channel_idx]):
                    coef = coeffs[channel_idx, fov_idx, nchanx[channel_idx, k]] * (
                            datasets[nchanx[channel_idx, k], :, fov_idx] -
                            amean[nchanx[channel_idx, k], fov_idx, channel_idx])
                    coeff_sum += coef
                new_ds[:, fov_idx] = coeff_sum + dmean[channel_idx]

        return all_new_ds

    def get_metadata(self, data, ds_info):
        """Get metadata."""
        metadata = {}
        metadata.update(data.attrs)
        metadata.update(ds_info)
        metadata.update({
            'sensor': self.sensor,
            'platform_name': self.platform_name,
            'start_time': self.start_time,
            'end_time': self.end_time,
        })

        return metadata

    def _rename_dims(self, data_arr):
        """Normalize dimension to x (pixel),y (lines) for Satpy."""
        dims_dict = {}
        if 'Field_of_view' in data_arr.dims:
            dims_dict['Field_of_view'] = 'x'
        if 'Scanline' in data_arr.dims:
            dims_dict['Scanline'] = 'y'
        return data_arr.rename(dims_dict)

    def get_dataset(self, ds_id, ds_info):
        """Get datasets."""
        data = self[ds_info.get('file_key', ds_info['name'])]
        data.attrs = self.get_metadata(data, ds_info)
        data = self._rename_dims(data)
        return data

    def available_datasets(self, configured_datasets=None):
        """Update information for or add datasets provided by this file."""
        handled_variables = set()
        # update previously configured datasets
        for is_avail, ds_info in (configured_datasets or []):
            # some other file handler knows how to load this
            if is_avail is not None:
                yield is_avail, ds_info

            var_name = ds_info.get('file_key', ds_info['name'])
            matches = self.file_type_matches(ds_info['file_type'])
            # we can confidently say that we can provide this dataset and can
            # provide more info
            if matches and var_name in self:
                handled_variables.add(var_name)
                new_info = ds_info.copy()  # don't mess up the above yielded
                yield True, new_info
            elif is_avail is None:
                # if we didn't know how to handle this dataset and no one else did
                # then we should keep it going down the chain
                yield is_avail, ds_info

        # Provide new datasets
        for var_name, val in self.file_content.items():
            if var_name in handled_variables:
                continue
            if isinstance(val, netCDF4.Variable):
                # get specific brightness temperature band products
                if (var_name == 'BT' and
                        self.filetype_info['file_type']
                        in ['mirs_atms', 'mirs_l2']):
                    freq = self['Freq']
                    polo = self['Polo']
                    from collections import Counter
                    c = Counter()
                    normals = []
                    for idx, (f, p) in enumerate(zip(freq, polo)):
                        normal_f = str(int(f))
                        normal_p = 'v' if p == POLO_V else 'h'
                        c[normal_f + normal_p] += 1
                        normals.append((idx, f, p, normal_f, normal_p))

                    c2 = Counter()
                    new_names = []
                    for _idx, _f, _p, normal_f, normal_p in normals:
                        c2[normal_f + normal_p] += 1
                        new_name = "btemp_{}{}{}".format(normal_f, normal_p, str(
                            c2[normal_f + normal_p] if c[normal_f + normal_p] > 1 else ''))
                        new_names.append(new_name)
                        var_name = 'bt_var_{}'.format(new_name)

                        # FIXME below is old p2g code
                        # FILE_STRUCTURE[var_name] = ("BT", ("scale", "scale_factor"), None, idx)
                        # self.PRODUCTS.add_product(new_name, PAIR_MIRS_NAV, "toa_brightness_temperature", FT_IMG,
                        #                           var_name,
                        #                           description="Channel Brightness Temperature at {}GHz".format(f),
                        #                           units="K", frequency=f,
                        #                           dependencies=(PRODUCT_BT_CHANS, PRODUCT_SURF_TYPE),
                        #                                         channel_index=idx)
                        # self.all_bt_channels.append(new_name)

                        ds_info = {
                            'file_type': self.filetype_info['file_type'],
                            'name': new_name,
                        }
                        yield True, ds_info

                else:
                    ds_info = {
                        'file_type': self.filetype_info['file_type'],
                        'name': var_name,
                    }
                    yield True, ds_info
