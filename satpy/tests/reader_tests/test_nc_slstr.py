#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2018 Satpy developers
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
"""Module for testing the satpy.readers.nc_slstr module."""
import unittest
import unittest.mock as mock
from satpy.dataset.dataid import WavelengthRange, ModifierTuple, DataID

local_id_keys_config = {'name': {
    'required': True,
},
    'wavelength': {
    'type': WavelengthRange,
},
    'resolution': None,
    'calibration': {
    'enum': [
        'reflectance',
        'brightness_temperature',
        'radiance',
        'counts'
    ]
},
    'stripe': {
        'enum': [
            'a',
            'b',
            'c',
            'i',
            'f',
        ]
    },
    'view': {
        'enum': [
            'nadir',
            'oblique',
        ]
    },
    'modifiers': {
    'required': True,
    'default': ModifierTuple(),
    'type': ModifierTuple,
},
}


def make_dataid(**items):
    """Make a data id."""
    return DataID(local_id_keys_config, **items)


class TestSLSTRReader(unittest.TestCase):
    """Test various nc_slstr file handlers."""

    @mock.patch('xarray.open_dataset')
    def test_instantiate(self, mocked_dataset):
        """Test initialization of file handlers."""
        from satpy.readers.slstr_l1b import NCSLSTR1B, NCSLSTRGeo, NCSLSTRAngles, NCSLSTRFlag

        ds_id = make_dataid(name='foo', calibration='radiance', stripe='a', view='nadir')
        filename_info = {'mission_id': 'S3A', 'dataset_name': 'foo', 'start_time': 0, 'end_time': 0,
                         'stripe': 'a', 'view': 'n'}
        test = NCSLSTR1B('somedir/S1_radiance_an.nc', filename_info, 'c')
        assert(test.view == 'nadir')
        assert(test.stripe == 'a')
        test.get_dataset(ds_id, dict(filename_info, **{'file_key': 'foo'}))
        mocked_dataset.assert_called()
        mocked_dataset.reset_mock()

        filename_info = {'mission_id': 'S3A', 'dataset_name': 'foo', 'start_time': 0, 'end_time': 0,
                         'stripe': 'c', 'view': 'o'}
        test = NCSLSTR1B('somedir/S1_radiance_co.nc', filename_info, 'c')
        assert(test.view == 'oblique')
        assert(test.stripe == 'c')
        test.get_dataset(ds_id, dict(filename_info, **{'file_key': 'foo'}))
        mocked_dataset.assert_called()
        mocked_dataset.reset_mock()

        filename_info = {'mission_id': 'S3A', 'dataset_name': 'foo', 'start_time': 0, 'end_time': 0,
                         'stripe': 'a', 'view': 'n'}
        test = NCSLSTRGeo('somedir/S1_radiance_an.nc', filename_info, 'c')
        test.get_dataset(ds_id, dict(filename_info, **{'file_key': 'foo'}))
        mocked_dataset.assert_called()
        mocked_dataset.reset_mock()

        test = NCSLSTRAngles('somedir/S1_radiance_an.nc', filename_info, 'c')
        # TODO: Make this test work
        # test.get_dataset(ds_id, filename_info)
        mocked_dataset.assert_called()
        mocked_dataset.reset_mock()

        test = NCSLSTRFlag('somedir/S1_radiance_an.nc', filename_info, 'c')
        assert(test.view == 'nadir')
        assert(test.stripe == 'a')
        mocked_dataset.assert_called()
        mocked_dataset.reset_mock()
