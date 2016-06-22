#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2015.

# Author(s):

#   David Hoese <david.hoese@ssec.wisc.edu>
#   Martin Raspaud <martin.raspaud@smhi.se>

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

"""Shared objects of the various reader classes.

"""

import logging
import numbers
import os
import numpy as np
import six
from abc import abstractmethod, abstractproperty, ABCMeta
from fnmatch import fnmatch
from collections import namedtuple
from datetime import datetime, timedelta
from trollsift.parser import globify, Parser

from satpy.plugin_base import Plugin
from satpy.projectable import Projectable
from satpy.config import runtime_import, config_search_paths, glob_config

try:
    import configparser
except ImportError:
    from six.moves import configparser
import glob

LOG = logging.getLogger(__name__)

DATASET_KEYS = ("name", "wavelength", "resolution", "polarization", "calibration")
DatasetID = namedtuple("Dataset", " ".join(DATASET_KEYS))
DatasetID.__new__.__defaults__ = (None, None, None, None, None)


class DatasetDict(dict):
    """Special dictionary object that can handle dict operations based on dataset name, wavelength, or DatasetID

    Note: Internal dictionary keys are `DatasetID` objects.
    """
    def __init__(self, *args, **kwargs):
        super(DatasetDict, self).__init__(*args, **kwargs)

    def keys(self, names=False, wavelengths=False):
        keys = super(DatasetDict, self).keys()
        if names:
            return (k.name for k in keys)
        elif wavelengths:
            return (k.wavelength for k in keys)
        else:
            return keys

    def _name_match(self, a, b):
        return a == b

    def _wl_match(self, a, b):
        if type(a) == type(b):
            return a == b
        elif isinstance(a, (list, tuple)) and len(a) == 3:
            return a[0] <= b <= a[2]
        elif isinstance(b, (list, tuple)) and len(b) == 3:
            return b[0] <= a <= b[2]
        else:
            raise ValueError("Can only compare wavelengths of length 1 or 3")

    def get_key(self, key):
        if isinstance(key, DatasetID):
            res = self.get_keys_by_datasetid(key)
            if not res:
                return None
            elif len(res) > 1:
                raise KeyError("No unique dataset matching " + str(key))
            else:
                return res[0]
        # get by wavelength
        elif isinstance(key, numbers.Number):
            for k in self.keys():
                if k.wavelength is not None and self._wl_match(k.wavelength, key):
                    return k
        # get by name
        else:
            for k in self.keys():
                if self._name_match(k.name, key):
                    return k

    def get_keys(self, name_or_wl, resolution=None, polarization=None, calibration=None):
        # Get things that match at least the name_or_wl
        if isinstance(name_or_wl, numbers.Number):
            keys = [k for k in self.keys() if self._wl_match(k.wavelength, name_or_wl)]
        elif isinstance(name_or_wl, (str, six.text_type)):
            keys = [k for k in self.keys() if self._name_match(k.name, name_or_wl)]
        else:
            raise TypeError("First argument must be a wavelength or name")

        if resolution is not None:
            if not isinstance(resolution, (list, tuple)):
                resolution = (resolution,)
            keys = [k for k in keys if k.resolution is not None and k.resolution in resolution]
        if polarization is not None:
            if not isinstance(polarization, (list, tuple)):
                polarization = (polarization,)
            keys = [k for k in keys if k.polarization is not None and k.polarization in polarization]
        if calibration is not None:
            if not isinstance(calibration, (list, tuple)):
                calibration = (calibration,)
            keys = [k for k in keys if k.calibration is not None and k.calibration in calibration]

        return keys

    def get_keys_by_datasetid(self, did):
        keys = self.keys()
        for key in DATASET_KEYS:
            if getattr(did, key) is not None:
                if key == "wavelength":
                    keys = [k for k in keys if getattr(k, key) is not None and self._wl_match(getattr(k, key),
                                                                                              getattr(did, key))]
                else:
                    keys = [k for k in keys if getattr(k, key) is not None and getattr(k, key) == getattr(did, key)]

        return keys

    def get_item(self, name_or_wl, resolution=None, polarization=None, calibration=None):
        keys = self.get_keys(name_or_wl, resolution=resolution, polarization=polarization, calibration=calibration)
        if not keys:
            raise KeyError("No keys found matching provided filters")

        return self[keys[0]]

    def __getitem__(self, item):
        key = self.get_key(item)
        if key is None:
            raise KeyError("No dataset matching '{}' found".format(str(item)))
        return super(DatasetDict, self).__getitem__(key)

    def __setitem__(self, key, value):
        """Support assigning 'Projectable' objects or dictionaries of metadata.
        """
        d = value.info if isinstance(value, Projectable) else value
        if not isinstance(key, DatasetID):
            old_key = key
            key = self.get_key(key)
            if key is None:
                if isinstance(old_key, (str, six.text_type)):
                    new_name = old_key
                else:
                    new_name = d.get("name")
                # this is a new key and it's not a full DatasetID tuple
                key = DatasetID(
                    name=new_name,
                    resolution=d.get("resolution"),
                    wavelength=d.get("wavelength_range"),
                    polarization=d.get("polarization"),
                    calibration=d.get("calibration"),
                )
                if key.name is None and key.wavelength is None:
                    raise ValueError("One of 'name' or 'wavelength_range' info values should be set.")

        # update the 'value' with the information contained in the key
        d["name"] = key.name
        # XXX: What should users be allowed to modify?
        d["resolution"] = key.resolution
        d["calibration"] = key.calibration
        d["polarization"] = key.polarization
        # you can't change the wavelength of a dataset, that doesn't make sense
        if "wavelength_range" in d and d["wavelength_range"] != key.wavelength:
            raise TypeError("Can't change the wavelength of a dataset")

        return super(DatasetDict, self).__setitem__(key, value)

    def __contains__(self, item):
        key = self.get_key(item)
        return super(DatasetDict, self).__contains__(key)

    def __delitem__(self, key):
        key = self.get_key(key)
        return super(DatasetDict, self).__delitem__(key)


class ReaderFinder(object):
    """Finds readers given a scene, filenames, sensors, and/or a reader_name
    """

    def __init__(self, ppp_config_dir=None, base_dir=None, **info):
        self.info = info
        self.ppp_config_dir = ppp_config_dir
        self.base_dir = base_dir

    def __call__(self, filenames=None, sensor=None, reader=None):
        if reader is not None:
            return [self._find_reader(reader, filenames)]
        elif sensor is not None:
            return list(self._find_sensors_readers(sensor, filenames))
        elif filenames is not None:
            return list(self._find_files_readers(filenames))
        return []

    def _find_sensors_readers(self, sensor, filenames):
        """Find the readers for the given *sensor* and *filenames*
        """
        if isinstance(sensor, (str, six.text_type)):
            sensor_set = set([sensor])
        else:
            sensor_set = set(sensor)

        reader_names = set()
        for config_file in glob_config(os.path.join("readers", "*.cfg"), self.ppp_config_dir):
            # This is just used to find the individual reader configurations, not necessarily the individual files
            config_fn = os.path.basename(config_file)
            if config_fn in reader_names:
                # we've already loaded this reader (even if we found it through another environment)
                continue

            try:
                config_files = config_search_paths(os.path.join("readers", config_fn), self.ppp_config_dir)
                reader_info = self._read_reader_config(config_files)
                LOG.debug("Successfully read reader config: %s", config_fn)
                reader_names.add(config_fn)
            except ValueError:
                LOG.debug("Invalid reader config found: %s", config_fn, exc_info=True)
                continue

            if "sensor" in reader_info and (set(reader_info["sensor"]) & sensor_set):
                # we want this reader
                if filenames:
                    # returns a copy of the filenames remaining to be matched
                    filenames = self.assign_matching_files(reader_info, *filenames, base_dir=self.base_dir)
                    if filenames:
                        raise IOError("Don't know how to open the following files: {}".format(str(filenames)))
                else:
                    # find the files for this reader based on its file patterns
                    reader_info["filenames"] = self.get_filenames(reader_info, self.base_dir)
                    if not reader_info["filenames"]:
                        LOG.warning("No filenames found for reader: %s", reader_info["name"])
                        continue
                yield self._load_reader(reader_info)

    def _find_reader(self, reader, filenames):
        """Find and get info for the *reader* for *filenames*
        """
        if not isinstance(reader, str):
            # we were given an instance of a reader or reader-like object
            return reader
        elif not os.path.exists(reader):
            # no, we were given a name of a reader
            config_fn = reader + ".cfg" if "." not in reader else reader
            config_files = config_search_paths(os.path.join("readers", config_fn), self.ppp_config_dir)
            if not config_files:
                raise ValueError("Can't find config file for reader: {}".format(reader))
        else:
            # we may have been given a dependent config file (depends on builtin configuration)
            # so we need to find the others
            config_fn = os.path.basename(reader)
            config_files = config_search_paths(os.path.join("readers", config_fn), self.ppp_config_dir)
            config_files.insert(0, reader)

        reader_info = self._read_reader_config(config_files)
        if filenames:
            filenames = self.assign_matching_files(reader_info, *filenames, base_dir=self.base_dir)
            if filenames:
                raise IOError("Don't know how to open the following files: {}".format(str(filenames)))
        else:
            reader_info["filenames"] = self.get_filenames(reader_info, base_dir=self.base_dir)
            if not reader_info["filenames"]:
                raise RuntimeError("No filenames found for reader: {}".format(reader_info["name"]))

        return self._load_reader(reader_info)

    def _find_files_readers(self, files):
        """Find the reader info for the provided *files*.
        """
        reader_names = set()
        for config_file in glob_config(os.path.join("readers", "*.cfg"), self.ppp_config_dir):
            # This is just used to find the individual reader configurations, not necessarily the individual files
            config_fn = os.path.basename(config_file)
            if config_fn in reader_names:
                # we've already loaded this reader (even if we found it through another environment)
                continue

            try:
                config_files = config_search_paths(os.path.join("readers", config_fn), self.ppp_config_dir)
                reader_info = self._read_reader_config(config_files)
                LOG.debug("Successfully read reader config: %s", config_fn)
                reader_names.add(config_fn)
            except ValueError:
                LOG.debug("Invalid reader config found: %s", config_fn, exc_info=True)
                continue

            files = self.assign_matching_files(reader_info, *files, base_dir=self.base_dir)

            if reader_info["filenames"]:
                # we have some files for this reader so let's create it
                yield self._load_reader(reader_info)

            if not files:
                break
        if files:
            raise IOError("Don't know how to open the following files: {}".format(str(files)))

    def get_filenames(self, reader_info, base_dir=None):
        """Get the filenames from disk given the patterns in *reader_info*.
        This assumes that the scene info contains start_time at least (possibly end_time too).
        """

        filenames = []
        info = self.info.copy()
        for key in self.info.keys():
            if key.endswith("_time"):
                info.pop(key, None)

        reader_start = reader_info["start_time"]
        reader_end = reader_info.get("end_time")
        if reader_start is None:
            raise ValueError("'start_time' keyword required with 'sensor' and 'reader' keyword arguments")

        for pattern in reader_info["file_patterns"]:
            if base_dir:
                pattern = os.path.join(base_dir, pattern)
            parser = Parser(str(pattern))
            # FIXME: what if we are browsing a huge archive ?
            for filename in glob.iglob(parser.globify(info.copy())):
                try:
                    metadata = parser.parse(filename)
                except ValueError:
                    LOG.info("Can't get any metadata from filename: %s from %s", pattern, filename)
                    metadata = {}
                if "end_time" in metadata and metadata["start_time"] > metadata["end_time"]:
                    mdate = metadata["start_time"].date()
                    mtime = metadata["end_time"].time()
                    if mtime < metadata["start_time"].time():
                        mdate += timedelta(days=1)
                    metadata["end_time"] = datetime.combine(mdate, mtime)
                meta_start = metadata.get("start_time", metadata.get("nominal_time"))
                meta_end = metadata.get("end_time", datetime(1950, 1, 1))
                if reader_end:
                    # get the data within the time interval
                    if ((reader_start <= meta_start <= reader_end) or
                            (reader_start <= meta_end <= reader_end)):
                        filenames.append(filename)
                else:
                    # get the data containing start_time
                    if "end_time" in metadata and meta_start <= reader_start <= meta_end:
                        filenames.append(filename)
                    elif meta_start == reader_start:
                        filenames.append(filename)
        return sorted(filenames)

    def _read_reader_config(self, config_files):
        """Read the reader *cfg_file* and return the info extracted.
        """
        conf = configparser.RawConfigParser()
        successes = conf.read(config_files)
        if not successes:
            raise ValueError("No valid configuration files found named: {}".format(config_files))
        LOG.debug("Read config from %s", str(successes))

        file_patterns = []
        sensors = set()
        reader_name = None
        reader_class = None
        reader_info = None
        # Only one reader: section per config file
        for section in conf.sections():
            if section.startswith("reader:"):
                reader_info = dict(conf.items(section))
                reader_info["file_patterns"] = filter(None, reader_info.setdefault("file_patterns", "").split(","))
                reader_info["sensor"] = filter(None, reader_info.setdefault("sensor", "").split(","))
                # XXX: Readers can have separate start/end times from the
                # rest fo the scene...might be a bad idea?
                reader_info.setdefault("start_time", self.info.get("start_time"))
                reader_info.setdefault("end_time", self.info.get("end_time"))
                reader_info.setdefault("area", self.info.get("area"))
                try:
                    reader_class = reader_info["reader"]
                    reader_name = reader_info["name"]
                except KeyError:
                    break
                file_patterns.extend(reader_info["file_patterns"])

                if reader_info["sensor"]:
                    sensors |= set(reader_info["sensor"])
            else:
                if conf.has_option(section, "file_patterns"):
                    file_patterns.extend(conf.get(section, "file_patterns").split(","))

                if conf.has_option(section, "sensor"):
                    sensors |= set(conf.get(section, "sensor").split(","))

        if reader_class is None:
            raise ValueError("Malformed config file {}: missing reader 'reader'".format(config_files))
        if reader_name is None:
            raise ValueError("Malformed config file {}: missing reader 'name'".format(config_files))
        reader_info["file_patterns"] = file_patterns
        reader_info["config_files"] = config_files
        reader_info["filenames"] = []
        reader_info["sensor"] = tuple(sensors)

        return reader_info

    @staticmethod
    def _load_reader(reader_info):
        """Import and setup the reader from *reader_info*
        """
        try:
            loader = runtime_import(reader_info["reader"])
        except ImportError as err:
            raise ImportError("Could not import reader class '{}' for reader '{}': {}".format(
                reader_info["reader"], reader_info["name"], str(err)))

        reader_instance = loader(**reader_info)
        # fixme: put this in the calling function
        # self.readers[reader_info["name"]] = reader_instance
        return reader_instance

    @staticmethod
    def assign_matching_files(reader_info, *files, **kwargs):
        """Assign *files* to the *reader_info*
        """
        files = list(files)
        for file_pattern in reader_info["file_patterns"]:
            if kwargs.get("base_dir"):
                file_pattern = os.path.join(kwargs["base_dir"], file_pattern)
            pattern = globify(file_pattern)
            for filename in list(files):
                if fnmatch(os.path.basename(filename), os.path.basename(pattern)):
                    reader_info["filenames"].append(filename)
                    files.remove(filename)

        # return remaining/unmatched files
        return files


class Reader(Plugin):
    """Reader plugins. They should have a *pformat* attribute, and implement
    the *load* method. This is an abstract class to be inherited.
    """
    splittable_dataset_options = ["file_patterns", "navigation", "standard_name", "units"]

    def __init__(self, name=None,
                 file_patterns=None,
                 filenames=None,
                 description="",
                 start_time=None,
                 end_time=None,
                 area=None,
                 sensor=None,
                 **kwargs):
        """The reader plugin takes as input a satellite scene to fill in.

        Arguments:
        - `scene`: the scene to fill.
        """
        # Hold information about datasets
        self.datasets = DatasetDict()
        self.metadata_info = {}

        # Load the config
        super(Reader, self).__init__(**kwargs)

        # Use options from the config file if they weren't passed as arguments
        self.name = self.config_options.get("name") if name is None else name
        self.file_patterns = self.config_options.get("file_patterns") if file_patterns is None else file_patterns
        self.filenames = self.config_options.get("filenames", []) if filenames is None else filenames
        self.description = self.config_options.get("description") if description is None else description
        self.sensor = self.config_options.get("sensor", "").split(",") if sensor is None else set(sensor)

        # These can't be provided by a configuration file
        self.start_time = start_time
        self.end_time = end_time
        self.area = area

        if self.name is None:
            raise ValueError("Reader 'name' not provided")

    def add_filenames(self, *filenames):
        self.filenames |= set(filenames)

    @property
    def dataset_names(self):
        """Names of all datasets configured for this reader.
        """
        return self.datasets.keys(names=True)

    @property
    def available_datasets(self):
        """Return what datasets can be loaded by what file types have been loaded.

        :return: generator of loadable dataset names
        """
        LOG.warning("Asking for available datasets from 'dumb' reader, all datasets being returned")
        return self.dataset_names

    @property
    def sensor_names(self):
        """Sensors supported by this reader.
        """
        sensors = set()
        for ds_info in self.datasets.values():
            if "sensor" in ds_info:
                sensors |= set(ds_info["sensor"].split(","))
        return sensors | self.sensor

    def load_section_reader(self, section_name, section_options):
        self.config_options = section_options

    def load_section_dataset(self, section_name, section_options):
        # required for Dataset identification
        section_options["resolution"] = tuple(float(res) for res in section_options.get("resolution").split(','))
        num_permutations = len(section_options["resolution"])

        # optional or not applicable for all datasets for Dataset identification
        if "wavelength_range" in section_options:
            section_options["wavelength_range"] = tuple(float(wvl) for wvl in section_options.get("wavelength_range").split(','))
        else:
            section_options["wavelength_range"] = None

        if "calibration" in section_options:
            section_options["calibration"] = tuple(section_options.get("calibration").split(','))
        else:
            section_options["calibration"] = [None] * num_permutations

        if "polarization" in section_options:
            section_options["polarization"] = tuple(section_options.get("polarization").split(','))
        else:
            section_options["polarization"] = [None] * num_permutations

        # Sanity checks
        assert "name" in section_options
        assert section_options["wavelength_range"] is None or (len(section_options["wavelength_range"]) == 3)
        assert num_permutations == len(section_options["calibration"])
        assert num_permutations == len(section_options["polarization"])

        # Add other options that are based on permutations
        for k in self.splittable_dataset_options:
            if k in section_options:
                section_options[k] = section_options[k].split(",")
            else:
                section_options[k] = [None]

        for k in self.splittable_dataset_options + ["calibration", "polarization"]:
            if len(section_options[k]) == 1:
                # if a single value is used for all permutations, repeat it
                section_options[k] *= num_permutations
            else:
                assert(num_permutations == len(section_options[k]))

        # Add each possible permutation of this dataset to the datasets list for later use
        for idx, (res, cal, pol) in enumerate(zip(section_options["resolution"],
                                                  section_options["calibration"],
                                                  section_options["polarization"])):
            bid = DatasetID(
                name=section_options["name"],
                wavelength=section_options["wavelength_range"],
                resolution=res,
                calibration=cal,
                polarization=pol,
            )

            opts = section_options.copy()
            # get only the specific permutations value that we want
            opts["id"] = bid
            for k in self.splittable_dataset_options + ["resolution", "calibration", "polarization"]:
                opts[k] = opts[k][idx]
            self.datasets[bid] = opts

    def load_section_metadata(self, section_name, section_options):
        name = section_name.split(":")[-1]
        self.metadata_info[name] = section_options

    def get_dataset_key(self, key, calibration=None, resolution=None, polarization=None, aslist=False):
        """Get the fully qualified dataset corresponding to *key*, either by name or centerwavelength.

        If `key` is a `DatasetID` object its name is searched if it exists, otherwise its wavelength is used.
        """
        # get by wavelength
        if isinstance(key, numbers.Number):
            datasets = [ds for ds in self.datasets.keys() if ds.wavelength and (ds.wavelength[0] <= key <= ds.wavelength[2])]
            datasets = sorted(datasets, key=lambda ch: abs(ch.wavelength[1] - key))

            if not datasets:
                raise KeyError("Can't find any projectable at %gum" % key)
        elif isinstance(key, DatasetID):
            if key.name is not None:
                datasets = self.get_dataset_key(key.name, aslist=True)
            elif key.wavelength is not None:
                datasets = self.get_dataset_key(key.wavelength, aslist=True)
            else:
                raise KeyError("Can't find any projectable '{}'".format(key))

            if calibration is not None:
                calibration = [key.calibration]
            if resolution is not None:
                resolution = [key.resolution]
            if polarization is not None:
                polarization = [key.polarization]
        # get by name
        else:
            datasets = [ds_id for ds_id in self.datasets.keys() if ds_id.name == key]
            if not datasets:
                raise KeyError("Can't find any projectable called '{}'".format(key))

        # default calibration choices
        if calibration is None:
            calibration = ["brightness_temperature", "reflectance"]

        if resolution is not None:
            datasets = [ds_id for ds_id in datasets if ds_id.resolution in resolution]
        if calibration is not None:
            # order calibration from highest level to lowest level
            calibration = [x for x in ["brightness_temperature", "reflectance", "radiance", "counts"] if x in calibration]
            datasets = [ds_id for ds_id in datasets if ds_id.calibration is None or ds_id.calibration in calibration]
        if polarization is not None:
            datasets = [ds_id for ds_id in datasets if ds_id.polarization in polarization]

        if not datasets:
            raise KeyError("Can't find any projectable matching '{}'".format(str(key)))
        if aslist:
            return datasets
        else:
            return datasets[0]

    def load(self, datasets_to_load):
        """Loads the *datasets_to_load* into the scene object.
        """
        raise NotImplementedError

    def load_metadata(self, datasets_to_load, metadata_to_load):
        """Load the specified metadata for the specified datasets.

        :returns: dictionary of dictionaries
        """
        raise NotImplementedError


class FileKey(namedtuple("FileKey", ["name", "variable_name", "scaling_factors", "offset",
                                     "dtype", "standard_name", "units", "file_units", "kwargs"])):
    def __new__(cls, name, variable_name,
                scaling_factors=None, offset=None,
                dtype=np.float32, standard_name=None, units=None, file_units=None, **kwargs):
        if isinstance(dtype, (str, six.text_type)):
            # get the data type from numpy
            dtype = getattr(np, dtype)
        return super(FileKey, cls).__new__(cls, name, variable_name, scaling_factors, offset,
                                           dtype, standard_name, units, file_units, kwargs)


class ConfigBasedReader(Reader):
    splittable_dataset_options = Reader.splittable_dataset_options + ["file_type", "file_key"]
    file_key_class = FileKey

    def __init__(self, default_file_reader=None, **kwargs):
        self.file_types = {}
        self.file_readers = {}
        self.file_keys = {}
        self.navigations = {}
        self.calibrations = {}

        # Load the configuration file and other defaults
        super(ConfigBasedReader, self).__init__(**kwargs)

        # Set up the default class for reading individual files
        self.default_file_reader = self.config_options.get("default_file_reader") if default_file_reader is None else default_file_reader
        if isinstance(self.default_file_reader, (str, six.text_type)):
            self.default_file_reader = self._runtime_import(self.default_file_reader)
        if self.default_file_reader is None:
            raise RuntimeError("'default_file_reader' is a required argument")

        # Determine what we know about the files provided and create file readers to read them
        file_types = self.identify_file_types(self.filenames)
        # TODO: Add ability to discover files when none are provided
        if not file_types:
            raise ValueError("No input files found matching the configured file types")

        num_files = 0
        for file_type_name, file_type_files in file_types.items():
            file_type_files = self._get_swathsegment(file_type_files)
            LOG.debug("File type %s has %d files after segment selection", file_type_name, len(file_type_files))

            if not file_type_files:
                raise IOError("No files matching!: " +
                              "Start time = " + str(self.start_time) +
                              "  End time = " + str(self.end_time))
            elif num_files and len(file_type_files) != num_files:
                raise IOError("Varying numbers of files found", file_type_name)
            else:
                num_files = len(file_type_files)

            file_reader = MultiFileReader(file_type_name, file_types[file_type_name], self.file_keys)
            self.file_readers[file_type_name] = file_reader

    @property
    def available_datasets(self):
        """Return what datasets can be loaded by what file types have been loaded.

        :return: generator of loadable dataset names
        """
        for ds_id in self.available_datasets_ids:
            yield ds_id.name

    @property
    def available_datasets_ids(self):
        """Return what datasets can be loaded by what file types have been loaded.

        :return: generator of loadable dataset names
        """
        for ds_id, ds_info in self.datasets.items():
            if ds_info["file_type"] in self.file_readers:
                yield ds_id

    def _get_swathsegment(self, file_readers):
        """Trim down amount of swath data to use with various filters.

        The filter options are provided during `__init__` as:

         - start_time/end_time: Filter swath by time of file
         - area: Filter swath by a geographic area
        """
        if self.area is not None:
            from trollsched.spherical import SphPolygon
            from trollsched.boundary import AreaBoundary

            lons, lats = self.area.get_boundary_lonlats()
            area_boundary = AreaBoundary((lons.side1, lats.side1),
                                         (lons.side2, lats.side2),
                                         (lons.side3, lats.side3),
                                         (lons.side4, lats.side4))
            area_boundary.decimate(500)
            contour_poly = area_boundary.contour_poly

        segment_readers = []
        for file_reader in file_readers:
            file_start = file_reader.start_time
            file_end = file_reader.end_time

            # Search for multiple granules using an area
            if self.area is not None:
                ring_lons, ring_lats = file_reader.ring_lonlats
                if ring_lons is None:
                    raise ValueError("Granule selection by area is not supported by this reader")
                coords = np.vstack((ring_lons, ring_lats))
                poly = SphPolygon(np.deg2rad(coords))
                if poly.intersection(contour_poly) is not None:
                    segment_readers.append(file_reader)
                continue

            if self.start_time is None:
                # if no start_time, assume no time filtering
                segment_readers.append(file_reader)
                continue

            # Search for single granule using time start
            if self.end_time is None:
                if file_start <= self.start_time <= file_end:
                    segment_readers.append(file_reader)
                    continue
            else:
                # search for multiple granules
                # check that granule start time is inside interval
                if self.start_time <= file_start <= self.end_time:
                    segment_readers.append(file_reader)
                    continue

                # check that granule end time is inside interval
                if self.start_time <= file_end <= self.end_time:
                    segment_readers.append(file_reader)
                    continue

        return sorted(segment_readers, key=lambda x: x.start_time)

    def _interpolate_navigation(self, lon, lat):
        return lon, lat

    def load_navigation(self, nav_name, extra_mask=None, dep_file_type=None):
        """Load the `nav_name` navigation.

        :param dep_file_type: file type of dataset using this navigation. Useful for subclasses to implement relative
                              navigation file loading
        """
        nav_info = self.navigations[nav_name]
        lon_key = nav_info["longitude_key"]
        lat_key = nav_info["latitude_key"]
        file_type = nav_info["file_type"]

        file_reader = self.file_readers[file_type]

        gross_lon_data = file_reader.get_swath_data(lon_key)
        gross_lat_data = file_reader.get_swath_data(lat_key)

        lon_data, lat_data = self._interpolate_navigation(gross_lon_data, gross_lat_data)
        if extra_mask is not None:
            lon_data = np.ma.masked_where(extra_mask, lon_data)
            lat_data = np.ma.masked_where(extra_mask, lat_data)

        # FIXME: Is this really needed/does it belong here? Can we have a dummy/simple object?
        from pyresample import geometry
        area = geometry.SwathDefinition(lons=lon_data, lats=lat_data)
        area_name = ("swath_" +
                     file_reader.start_time.isoformat() + "_" +
                     file_reader.end_time.isoformat() + "_" +
                     "_" + "_".join(str(x) for x in lon_data.shape))
        # FIXME: Which one is used now:
        area.area_id = area_name
        area.name = area_name
        area.info = nav_info.copy()

        return area

    def identify_file_types(self, filenames, default_file_reader=None):
        """Identify the type of a file by its filename or by its contents.

        Uses previously loaded information from the configuration file.
        """
        file_types = {}
        # remaining_filenames = [os.path.basename(fn) for fn in filenames]
        remaining_filenames = filenames[:]
        for file_type_name, file_type_info in self.file_types.items():
            file_types[file_type_name] = []

            if default_file_reader is None:
                file_reader_class = file_type_info.get("file_reader", self.default_file_reader)
            else:
                file_reader_class = default_file_reader

            if isinstance(file_reader_class, (str, six.text_type)):
                file_reader_class = self._runtime_import(file_reader_class)
            for file_pattern in file_type_info["file_patterns"]:
                tmp_remaining = []
                tmp_matching = []
                for fn in remaining_filenames:
                    # Add a wildcard to the front for path information
                    # FIXME: Is there a better way to generalize this besides removing the path every time
                    if fnmatch(fn, "*" + globify(file_pattern)):
                        reader = file_reader_class(file_type_name, fn, self.file_keys, **file_type_info)
                        tmp_matching.append(reader)
                    else:
                        tmp_remaining.append(fn)

                file_types[file_type_name].extend(tmp_matching)
                remaining_filenames = tmp_remaining

            if not file_types[file_type_name]:
                del file_types[file_type_name]

            if not remaining_filenames:
                break

        for remaining_filename in remaining_filenames:
            LOG.warning("Unidentified file: %s", remaining_filename)

        return file_types

    def load_section_file_type(self, section_name, section_options):
        name = section_name.split(":")[-1]
        section_options["file_patterns"] = section_options["file_patterns"].split(",")
        # Don't create the file reader object yet
        self.file_types[name] = section_options

    def load_section_file_key(self, section_name, section_options):
        name = section_name.split(":")[-1]
        self.file_keys[name] = self.file_key_class(name=name, **section_options)

    def load_section_navigation(self, section_name, section_options):
        name = section_name.split(":")[-1]
        if "rows_per_scan" in section_options:
            section_options["rows_per_scan"] = int(section_options["rows_per_scan"])
        self.navigations[name] = section_options

    def load_section_calibration(self, section_name, section_options):
        name = section_name.split(":")[-1]
        self.calibrations[name] = section_options

    def _get_dataset_info(self, ds_id, calibration):
        dataset_info = self.datasets[ds_id].copy()

        if not dataset_info.get("calibration"):
            LOG.debug("No calibration set for '%s'", ds_id)
            dataset_info["file_type"] = dataset_info["file_type"][0]
            dataset_info["file_key"] = dataset_info["file_key"][0]
            dataset_info["navigation"] = dataset_info["navigation"][0]
            return dataset_info

        # Remove any file types and associated calibration, file_key, navigation if file_type is not loaded
        for k in ["file_type", "file_key", "calibration", "navigation"]:
            dataset_info[k] = []
        for idx, ft in enumerate(self.datasets[ds_id]["file_type"]):
            if ft in self.file_readers:
                for k in ["file_type", "file_key", "calibration", "navigation"]:
                    dataset_info[k].append(self.datasets[ds_id][k][idx])

        # By default do the first calibration for a dataset
        cal_index = 0
        cal_name = dataset_info["calibration"][0]
        for idx, cname in enumerate(dataset_info["calibration"]):
            # is this the calibration we want for this channel?
            if cname in calibration:
                cal_index = idx
                cal_name = cname
                LOG.debug("Using calibration '%s' for dataset '%s'", cal_name, ds_id)
                break
        else:
            LOG.debug("Using default calibration '%s' for dataset '%s'", cal_name, ds_id)

        # Load metadata and calibration information for this dataset
        try:
            cal_info = self.calibrations.get(cal_name)
            for k, info_dict in [("file_type", self.file_types),
                                 ("file_key", self.file_keys),
                                 ("navigation", self.navigations),
                                 ("calibration", self.calibrations)]:
                val = dataset_info[k][cal_index]
                if cal_info is not None:
                    val = cal_info.get(k, val)

                if val not in info_dict and k != "calibration":
                    # We don't care if the calibration has its own section
                    raise RuntimeError("Unknown '{}': {}".format(k, val,))
                dataset_info[k] = val

                if k == "file_key":
                    # collect any other metadata
                    dataset_info["standard_name"] = self.file_keys[val].standard_name
                    # dataset_info["file_units"] = self.file_keys[val].file_units
                    # dataset_info["units"] = self.file_keys[val].units
        except (IndexError, KeyError):
            raise RuntimeError("Could not get information to perform calibration '{}'".format(cal_name))

        return dataset_info

    def load_metadata(self, datasets_to_load, areas_to_load, metadata_to_load):
        """Load the specified metadata for the specified datasets.

        :returns: dictionary of dictionaries
        """
        loaded_metadata = {}
        if metadata_to_load is None:
            return loaded_metadata

        metadata_to_load = set(metadata_to_load) & set(self.metadata_info.keys())
        datasets_to_load = set(datasets_to_load) & set(self.datasets.keys())

        if not datasets_to_load:
            LOG.debug("No datasets from this reader for loading metadata")
            return loaded_metadata

        if not metadata_to_load:
            LOG.debug("No metadata to load from this reader")
            return loaded_metadata

        # For each dataset provided, get the specified metadata
        for ds_id in datasets_to_load:
            ds_info = self.datasets[ds_id]
            nav_info = self.navigations[ds_info["navigation"]]

            loaded_metadata[ds_id] = metadata_dict = {}
            area_metadata = metadata_dict.setdefault("_area_metadata", {})
            for metadata_id in metadata_to_load:
                md_info = self.metadata_info[metadata_id]
                file_type = md_info.get("file_type", "DATASET")
                file_key = md_info["file_key"]
                destination = md_info.get("destination", "DATASET")

                # special keys for using the datasets file type or the dataset's navigation file type
                if file_type == "DATASET":
                    file_type = ds_info["file_type"]
                elif file_type == "NAVIGATION":
                    file_type = nav_info["file_type"]

                if file_type not in self.file_readers:
                    LOG.warning("File type '%s' not loaded for metadata '%s'", file_type, metadata_id)
                    continue
                file_reader = self.file_readers[file_type]
                try:
                    md = file_reader.load_metadata(file_key,
                                                   join_method=md_info.get("join_method", "append"),
                                                   axis=int(md_info.get("axis", "0")))
                    if destination == "AREA":
                        area_metadata[metadata_id] = md
                    else:
                        metadata_dict[metadata_id] = md
                except (KeyError, ValueError):
                    LOG.debug("Could not load metadata '%s' for dataset '%s'", metadata_id, ds_id, exc_info=True)
                    continue

        return loaded_metadata

    def load(self, datasets_to_load, metadata=None, **dataset_info):
        if dataset_info:
            LOG.warning("Unsupported options for viirs reader: %s", str(dataset_info))

        datasets_loaded = DatasetDict()
        datasets_to_load = set(datasets_to_load) & set(self.datasets.keys())
        if not datasets_to_load:
            LOG.debug("No datasets to load from this reader")
            return datasets_loaded

        LOG.debug("Channels to load: " + str(datasets_to_load))

        # Sanity check and get the navigation sets being used
        areas = {}
        for ds_id in datasets_to_load:
            dataset_info = self.datasets[ds_id]
            calibration = dataset_info["calibration"]

            # if there is a calibration section in the config, use that for more information
            # FIXME: We also need to get units and other information...and make it easier to do that, per attribute method?
            # Or maybe a post-configuration load method...that's probably best
            if calibration in self.calibrations:
                cal_info = self.calibrations[calibration]
                file_type = cal_info["file_type"]
                file_key = cal_info["file_key"]
                nav_name = cal_info["navigation"]
            else:
                file_type = dataset_info["file_type"]
                file_key = dataset_info["file_key"]
                nav_name = dataset_info["navigation"]
            try:
                file_reader = self.file_readers[file_type]
            except KeyError:
                LOG.warning("Can't file any file for type: %s", str(file_type))
                continue

            # Get the swath data (fully scaled and in the correct data type)
            data = file_reader.get_swath_data(file_key)

            # Load the navigation information first
            if nav_name not in areas:
                areas[nav_name] = area = self.load_navigation(nav_name, dep_file_type=file_type)
            else:
                area = areas[nav_name]

            # Create a projectable from info from the file data and the config file
            # FIXME: Remove metadata that is reader only
            if not dataset_info.get("units"):
                dataset_info["units"] = file_reader.get_units(file_key)
            dataset_info.setdefault("platform", file_reader.platform_name)
            dataset_info.setdefault("sensor", file_reader.sensor_name)
            dataset_info.setdefault("start_orbit", file_reader.begin_orbit_number)
            dataset_info.setdefault("end_orbit", file_reader.end_orbit_number)
            if "rows_per_scan" in self.navigations[nav_name]:
                dataset_info.setdefault("rows_per_scan", self.navigations[nav_name]["rows_per_scan"])
            projectable = Projectable(data=data,
                                      start_time=file_reader.start_time,
                                      end_time=file_reader.end_time,
                                      **dataset_info)
            projectable.info["area"] = area

            datasets_loaded[projectable.info["id"]] = projectable

        # Load metadata for all of the datasets
        loaded_metadata = self.load_metadata(datasets_loaded.keys(), areas, metadata)
        for ds_id, metadata_info in loaded_metadata.items():
            area_metadata = metadata_info.pop("_area_metadata")
            datasets_loaded[ds_id].info.update(metadata_info)

            for k, v in area_metadata.items():
                setattr(datasets_loaded[ds_id].info["area"], k, v)
        return datasets_loaded


class MultiFileReader(object):
    # FIXME: file_type isn't used here. Do we really need it ?
    def __init__(self, file_type, file_readers, file_keys, **kwargs):
        """
        :param file_type:
        :param file_readers: is a list of the reader instances to use.
        :param file_keys:
        :param kwargs:
        :return:
        """
        self.file_type = file_type
        self.file_readers = file_readers
        self.file_keys = file_keys

    @property
    def filenames(self):
        return [fr.filename for fr in self.file_readers]

    @property
    def start_time(self):
        return self.file_readers[0].start_time

    @property
    def end_time(self):
        return self.file_readers[-1].end_time

    @property
    def ring_lonlats(self):
        return [fr.ring_lonlats for fr in self.file_readers]

    @property
    def begin_orbit_number(self):
        return self.file_readers[0].begin_orbit_number

    @property
    def end_orbit_number(self):
        return self.file_readers[-1].end_orbit_number

    @property
    def platform_name(self):
        return self.file_readers[0].platform_name

    @property
    def sensor_name(self):
        return self.file_readers[0].sensor_name

    @property
    def geofilenames(self):
        return [fr.geofilename for fr in self.file_readers]

    def get_units(self, item):
        return self.file_readers[0].get_units(item)

    def get_swath_data(self, item, filename=None):
        var_info = self.file_keys[item]
        granule_shapes = [x.get_shape(item) for x in self.file_readers]
        num_rows = sum([x[0] for x in granule_shapes])
        if len(granule_shapes[0]) < 2:
            num_cols = 1
            output_shape = (num_rows,)
        else:
            num_cols = granule_shapes[0][-1]
            output_shape = (num_rows, num_cols)

        if filename:
            raise NotImplementedError("Saving data arrays to disk is not supported yet")
            # data = np.memmap(filename, dtype=var_info.dtype, mode='w', shape=(num_rows, num_cols))
        else:
            data = np.empty(output_shape, dtype=var_info.dtype)
            mask = np.zeros_like(data, dtype=np.bool)

        idx = 0
        for granule_shape, file_reader in zip(granule_shapes, self.file_readers):
            # Get the data from each individual file reader (assumes it gets the data with the right data type)
            file_reader.get_swath_data(item,
                                       data_out=data[idx: idx + granule_shape[0]],
                                       mask_out=mask[idx: idx + granule_shape[0]])
            idx += granule_shape[0]

        # FIXME: This may get ugly when using memmaps, maybe move projectable creation here instead
        return np.ma.array(data, mask=mask, copy=False)

    def load_metadata(self, item, join_method="append", axis=0):
        if join_method not in ["append", "extend_granule", "append_granule", "first"]:
            raise ValueError("Unknown metadata 'join_method': {}".format(join_method))
        elif join_method == "extend_granule":
            # we expect a list from the file reader
            return np.concatenate(tuple(fr[item] for fr in self.file_readers), axis=axis)
        elif join_method == "append_granule":
            return np.concatenate(tuple([fr[item]] for fr in self.file_readers), axis=axis)
        elif join_method == "append":
            return np.concatenate(tuple(fr[item] for fr in self.file_readers), axis=axis)
        elif join_method == "first":
            return self.file_readers[0][item]


class GenericFileReader(object):
    """Base class for individual file reader classes.

    This class provides an interface so that subclasses can be used via
    a `MultiFileReader` which is then used by a `ConfigBasedReader`
    subclass. This interface currently assumes certain file keys are
    available in the file being read. If these keys are not available
    or they have a different name then the method mentioned must be
    overridden in the subclass.

    Required File Keys:

     - coverage_start: Datetime string of the start time of the data
                       observation (see `_get_start_time`)
     - coverage_end: Datetime string of the end time of the data
                     observation (see `_get_end_time`)

    Start time and end time must be available so that files of the same
    type can be sorted in chronological order.

    The primary methods for data retrieval are `__getitem__` which is used
    for attribute and simple variable access and `get_swath_data` for larger
    swath variables that may require scaling, unit conversion, efficient
    appending to other granules, and separate masked arrays.
    """
    __metaclass__ = ABCMeta

    def __init__(self, file_type, filename, file_keys, **kwargs):
        self.file_type = file_type
        self.file_keys = file_keys
        self.file_info = kwargs
        self.filename, self.file_handle = self.create_file_handle(filename, **kwargs)

        # need to "cache" these properties because they might be used a lot
        self._start_time = self._get_start_time()
        self._end_time = self._get_end_time()

    @property
    def start_time(self):
        """Start time of the swath coverage.

        Note: `_get_start_time` should be overridden instead of this property.
        """
        return self._start_time

    @property
    def end_time(self):
        """End time of the swath coverage.

        Note: `_get_end_time` should be overridden instead of this property.
        """
        return self._end_time

    def _parse_datetime(self, date_str):
        """Return datetime object by parsing the provided string(s).
        """
        raise NotImplementedError

    def _get_start_time(self):
        """Return start time as datetime object.
        """
        return self._parse_datetime(self['coverage_start'])

    def _get_end_time(self):
        """Return end time as datetime object.
        """
        return self._parse_datetime(self['coverage_end'])

    @abstractmethod
    def create_file_handle(self, filename, **kwargs):
        # return tuple (filename, file_handle)
        raise NotImplementedError

    @abstractmethod
    def __getitem__(self, item):
        raise NotImplementedError

    @property
    def ring_lonlats(self):
        return None, None

    @property
    def begin_orbit_number(self):
        return 0

    @property
    def end_orbit_number(self):
        return 0

    @abstractproperty
    def platform_name(self):
        raise NotImplementedError

    @abstractproperty
    def sensor_name(self):
        raise NotImplementedError

    @property
    def geofilename(self):
        return None

    @abstractmethod
    def get_shape(self, item):
        raise NotImplementedError

    @abstractmethod
    def get_file_units(self, item):
        raise NotImplementedError

    def get_units(self, item):
        units = self.file_keys[item].units
        file_units = self.get_file_units(item)
        # What units does the user want
        if units is None:
            # if the units in the file information
            return file_units
        return units

    @abstractmethod
    def get_swath_data(self, item, data_out=None, mask_out=None):
        raise NotImplementedError

