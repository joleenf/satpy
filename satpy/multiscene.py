#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2016

# Author(s):

#   Martin Raspaud <martin.raspaud@smhi.se>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""MultiScene object to blend satellite data.
"""

import logging
import dask.array as da
from satpy.scene import Scene
from satpy.writers import get_enhanced_image

log = logging.getLogger(__name__)


def stack(datasets):
    """First dataset at the bottom."""
    base = datasets[0].copy()
    for dataset in datasets[1:]:
        base = base.where(dataset.isnull(), dataset)
    return base


class MultiScene(object):
    """Container for multiple `Scene` objects."""

    def __init__(self, layers):
        """Initialize MultiScene and validate sub-scenes"""
        self.scenes = layers

    @property
    def loaded_dataset_ids(self):
        """Union of all Dataset IDs loaded by all children."""
        return set(ds_id for scene in self.scenes for ds_id in scene.keys())

    @property
    def shared_dataset_ids(self):
        """Dataset IDs shared by all children."""
        shared_ids = set(self.scenes[0].keys())
        for scene in self.scenes[1:]:
            shared_ids &= set(scene.keys())
        return shared_ids

    @property
    def all_same_area(self):
        all_areas = [ds.attrs.get('area', None)
                     for scn in self.scenes for ds in scn]
        all_areas = [area for area in all_areas if area is not None]
        return all(all_areas[0] == area for area in all_areas[1:])

    def load(self, *args, **kwargs):
        """Load the required datasets from the multiple scenes."""
        for layer in self.scenes:
            layer.load(*args, **kwargs)

    def resample(self, destination, **kwargs):
        """Resample the multiscene."""
        return self.__class__([scn.resample(destination, **kwargs)
                               for scn in self.scenes])

    def blend(self, blend_function=stack):
        """Blend the datasets into one scene."""
        new_scn = Scene()
        common_datasets = self.shared_dataset_ids
        for ds_id in common_datasets:
            datasets = [scn[ds_id] for scn in self.scenes if ds_id in scn]
            new_scn[ds_id] = blend_function(datasets)

        return new_scn

    def _get_animation_info(self, all_datasets, filename, fill_value=None):
        """Determine filename and shape of animation to be created."""
        first_dataset = [ds for ds in all_datasets if ds is not None][0]
        first_img = get_enhanced_image(first_dataset)
        first_img_data = first_img._finalize(fill_value=fill_value)[0]
        shape = tuple(first_img_data.sizes.get(dim_name)
                      for dim_name in ('y', 'x', 'bands'))
        if fill_value is None and filename.endswith('gif'):
            log.warning("Forcing fill value to '0' for GIF Luminance images")
            fill_value = 0
            shape = shape[:2]

        this_fn = filename.format(**first_dataset.attrs)
        return this_fn, shape, fill_value

    def save(self, filename, datasets=None, fps=10, fill_value=None, **kwargs):
        """Helper method for saving to movie or GIF formats.

        Supported formats are dependent on the `imageio` library and are
        determined by filename extension by default.

        By default all datasets available will be saved to individual files
        using the first Scene's datasets metadata to format the filename
        provided. If a dataset is not available from a Scene then a black
        array is used instead (np.zeros(shape)).

        Args:
            filename (str): Filename to save to. Can include python string
                            formatting keys from dataset ``.attrs``
                            (ex. "{name}_{start_time:%Y%m%d_%H%M%S.gif")
            datasets (list): DatasetIDs to save (default: all datasets)
            fill_value (int): Value to use instead creating an alpha band.
            fps (int): Frames per second for produced animation
            **kwargs: Additional keyword arguments to pass to
                     `imageio.get_writer`.

        """
        import imageio
        if not self.all_same_area:
            raise ValueError("Sub-scenes must all be on the same area "
                             "(see the 'resample' method).")

        dataset_ids = datasets or self.loaded_dataset_ids
        for dataset_id in dataset_ids:
            all_datasets = [scn[dataset_id] for scn in self.scenes]
            this_fn, shape, fill_value = self._get_animation_info(
                all_datasets, filename, fill_value=fill_value)
            writer = imageio.get_writer(this_fn, fps=fps, **kwargs)

            for ds in all_datasets:
                if ds is None:
                    data = da.zeros(shape)
                else:
                    img = get_enhanced_image(ds)
                    data, mode = img._finalize(fill_value=fill_value)
                    if data.ndim == 3:
                        # assume all other shapes are (y, x)
                        # we need arrays grouped by pixel so
                        # transpose if needed
                        data = data.transpose('y', 'x', 'bands')
                writer.append_data(data.values)
            writer.close()
