#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2014, 2015, 2016 Adam.Dybbroe

# Author(s):

#   Adam.Dybbroe <adam.dybbroe@smhi.se>
#   Cooke, Michael.C, UK Met Office
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

"""HRIT format reader

"""

import logging
from datetime import datetime, timedelta

import numpy as np

from pyresample import geometry
from satpy.projectable import Projectable
from satpy.readers.hrit_base import (HRITFileHandler, ancillary_text,
                                     annotation_header, base_hdr_map,
                                     image_data_function, make_time_cds_short,
                                     time_cds_short)


class CalibrationError(Exception):
    pass

logger = logging.getLogger('hrit_msg')


# MSG implementation:
key_header = np.dtype([('key_number', 'u1'),
                       ('seed', '>f8')])

segment_identification = np.dtype([('GP_SC_ID', '>i2'),
                                   ('spectral_channel_id', '>i1'),
                                   ('segment_sequence_number', '>u2'),
                                   ('planned_start_segment_number', '>u2'),
                                   ('planned_end_segment_number', '>u2'),
                                   ('data_field_representation', '>i1')])

image_segment_line_quality = np.dtype([('line_number_in_grid', '>i4'),
                                       ('line_mean_acquisition',
                                        [('days', '>u2'),
                                         ('milliseconds', '>u4')]),
                                       ('line_validity', 'u1'),
                                       ('line_radiometric_quality', 'u1'),
                                       ('line_geometric_quality', 'u1')])

msg_variable_length_headers = {
    image_segment_line_quality: 'image_segment_line_quality'}

msg_text_headers = {image_data_function: 'image_data_function',
                    annotation_header: 'annotation_header',
                    ancillary_text: 'ancillary_text'}

msg_hdr_map = base_hdr_map.copy()
msg_hdr_map.update({7: key_header,
                    128: segment_identification,
                    129: image_segment_line_quality
                    })


orbit_coef = np.dtype([('StartTime', time_cds_short),
                       ('EndTime', time_cds_short),
                       ('X', '>f8', (8, )),
                       ('Y', '>f8', (8, )),
                       ('Z', '>f8', (8, )),
                       ('VX', '>f8', (8, )),
                       ('VY', '>f8', (8, )),
                       ('VZ', '>f8', (8, ))])

attitude_coef = np.dtype([('StartTime', time_cds_short),
                          ('EndTime', time_cds_short),
                          ('XofSpinAxis', '>f8', (8, )),
                          ('YofSpinAxis', '>f8', (8, )),
                          ('ZofSpinAxis', '>f8', (8, ))])

cuc_time = np.dtype([('coarse', 'u1', (4, )),
                     ('fine', 'u1', (3, ))])

time_cds_expanded = np.dtype([('days', '>u2'),
                              ('milliseconds', '>u4'),
                              ('microseconds', '>u2'),
                              ('nanoseconds', '>u2')])


def make_time_cds_expanded(tcds_array):
    return (datetime(1958, 1, 1) +
            timedelta(days=int(tcds_array['days']),
                      milliseconds=int(tcds_array['milliseconds']),
                      microseconds=float(tcds_array['microseconds'] +
                                         tcds_array['nanoseconds'] / 1000.)))

satellite_status = np.dtype([('SatelliteDefinition',
                              [('SatelliteID', '>u2'),
                               ('NominalLongitude', '>f4'),
                                  ('SatelliteStatus', '>u1')]),
                             ('SatelliteOperations',
                              [('LastManoeuvreFlag', 'b'),
                               ('LastManoeuvreStartTime', time_cds_short),
                                  ('LastManoeuvreEndTime', time_cds_short),
                                  ('LastManoeuvreType', '>u1'),
                                  ('NextManoeuvreFlag', 'b'),
                                  ('NextManoeuvreStartTime', time_cds_short),
                                  ('NextManoeuvreEndTime', time_cds_short),
                                  ('NextManoeuvreType', '>u1')]),
                             ('Orbit',
                              [('PeriodStartTime', time_cds_short),
                               ('PeriodEndTime', time_cds_short),
                                  ('OrbitPolynomial', orbit_coef, (100, ))]),
                             ('Attitude',
                              [('PeriodStartTime', time_cds_short),
                               ('PeriodEndTime', time_cds_short),
                                  ('PrincipleAxisOffsetAngle', 'f8'),
                                  ('AttitudePolynomial', attitude_coef, (100, ))]),
                             ('SpinRateatRCStart', '>f8'),
                             ('UTCCorrelation',
                              [('PeriodStartTime', time_cds_short),
                               ('PeriodEndTime', time_cds_short),
                                  ('OnBoardTimeStart', cuc_time),
                                  ('VarOnBoardTimeStart', '>f8'),
                                  ('A1', '>f8'),
                                  ('VarA1', '>f8'),
                                  ('A2', '>f8'),
                                  ('VarA2', '>f8')])])

image_acquisition = np.dtype([('PlannedAcquisitionTime',
                               [('TrueRepeatCycleStart', time_cds_expanded),
                                ('PlannedForwardScanEnd', time_cds_expanded),
                                ('PlannedRepeatCycleEnd', time_cds_expanded)]),
                              ('RadiometerStatus',
                               [('ChannelStatus', 'u1', (12, )),
                                ('DetectorStatus', 'u1', (42, ))]),
                              ('RadiometerSettings',
                               [('MDUSamplingDelays', '>u2', (42, )),
                                ('HRVFrameOffsets',
                                 [('MDUNomHRVDelay1', '>u2'),
                                  ('MDUNomHRVDelay2', '>u2'),
                                  ('Spare', '>u2'),
                                  ('MDUNomHRVBreakline', '>u2')]),
                                ('DHSSynchSelection', 'u1'),
                                ('MDUOutGain', '>u2', (42, )),
                                ('MDUCoarseGain', 'u1', (42, )),
                                ('MDUFineGain', '>u2', (42, )),
                                ('MDUNumericalOffset', '>u2', (42, )),
                                ('PUGain', '>u2', (42, )),
                                ('PUOffset', '>u2', (27, )),
                                ('PUBias', '>u2', (15, )),
                                ('OperationParameters',
                                 [('L0_LineCounter', '>u2'),
                                  ('K1_RetraceLines', '>u2'),
                                  ('K2_PauseDeciseconds', '>u2'),
                                  ('K3_RetraceLines', '>u2'),
                                  ('K4_PauseDeciseconds', '>u2'),
                                  ('K5_RetraceLines', '>u2'),
                                  ('X_DeepSpaceWindowPosition', 'u1')]),
                                ('RefocusingLines', '>u2'),
                                ('RefocusingDirection', 'u1'),
                                ('RefocusingPosition', '>u2'),
                                ('ScanRefPosFlag', 'bool'),
                                ('ScanRefPosNumber', '>u2'),
                                ('ScanRefPotVal', '>f4'),
                                ('ScanFirstLine', '>u2'),
                                ('ScanLastLine', '>u2'),
                                ('RetraceStartLine', '>u2')]),
                              ('RadiometerOperations',
                               [('LastGainChangeFlag', 'bool'),
                                ('LastGainChangeTime', time_cds_short),
                                ('Decontamination',
                                 [('DecontaminationNow', 'bool'),
                                  ('DecontaminationStart', time_cds_short),
                                  ('DecontaminationEnd', time_cds_short)]),
                                ('BBCalScheduled', 'bool'),
                                ('BBCalibrationType', 'u1'),
                                ('BBFirstLine', '>u2'),
                                ('BBLastLine', '>u2'),
                                ('ColdFocalPlaneOpTemp', '>u2'),
                                ('WarmFocalPlaneOpTemp', '>u2')])])

earth_moon_sun_coeff = np.dtype([('StartTime', time_cds_short),
                                 ('EndTime', time_cds_short),
                                 ('AlphaCoef', '>f8', (8, )),
                                 ('BetaCoef', '>f8', (8, ))])

star_coeff = np.dtype([('StarId', '>u2'),
                       ('StartTime', time_cds_short),
                       ('EndTime', time_cds_short),
                       ('AlphaCoef', '>f8', (8, )),
                       ('BetaCoef', '>f8', (8, ))])

celestial_events = np.dtype([('CelestialBodiesPosition',
                              [('PeriodStartTime', time_cds_short),
                               ('PeriodEndTime', time_cds_short),
                               ('RelatedOrbitFileTime', '|S15'),
                               ('RelatedAttitudeFileTime', '|S15'),
                               ('EarthEphemeris', earth_moon_sun_coeff, (100, )),
                               ('MoonEphemeris', earth_moon_sun_coeff, (100, )),
                               ('SunEphemeris', earth_moon_sun_coeff, (100, )),
                               ('StarEphemeris', star_coeff, (100, 20))]),
                             ('RelationToImage',
                              [('TypeOfEclipse', 'u1'),
                               ('EclipseStartTime', time_cds_short),
                               ('EclipseEndTime', time_cds_short),
                               ('VisibleBodiesInImage', 'u1'),
                               ('BodiesClosetoFOV', 'u1'),
                               ('ImpactOnImageQuality', 'u1')])])


image_description = np.dtype([('ProjectionDescription',
                               [('TypeOfProjection', 'u1'),
                                ('LongitudeOfSSP', '>f4')]),
                              ('ReferenceGridVIS_IR',
                               [('NumberOfLines', '>i4'),
                                ('NumberOfColumns', '>i4'),
                                ('LineDirGridStep', '>f4'),
                                ('ColumnDirGridStep', '>f4'),
                                ('GridOrigin', 'u1')]),
                              ('ReferenceGridHRV',
                               [('NumberOfLines', '>i4'),
                                ('NumberOfColumns', '>i4'),
                                ('LineDirGridStep', '>f4'),
                                ('ColumnDirGridStep', '>f4'),
                                ('GridOrigin', 'u1')]),
                              ('PlannedCoverageVIR_IR',
                               [('SouthernLinePlanned', '>i4'),
                                ('NorthernLinePlanned', '>i4'),
                                ('EasternColumnPlanned', '>i4'),
                                ('WesternColumnPlanned', '>i4')]),
                              ('PlannedCoverageHRV',
                               [('LowerSouthernLinePlanned', '>i4'),
                                ('LowerNorthernLinePlanned', '>i4'),
                                ('LowerEasternColumnPlanned', '>i4'),
                                ('LowerWesternColumnPlanned', '>i4'),
                                ('UpperSouthernLinePlanned', '>i4'),
                                ('UpperNorthernLinePlanned', '>i4'),
                                ('UpperEasternColumnPlanned', '>i4'),
                                ('UpperWesternColumnPlanned', '>i4')]),
                              ('Level1_5ImageProduction',
                               [('ImageProcDirection', 'u1'),
                                ('PixelGenDirection', 'u1'),
                                ('PlannedChanProcessing', 'u1', (12, ))])])

level_1_5_image_calibration = np.dtype([('Cal_Slope', '>f8'),
                                        ('Cal_Offset', '>f8')])

impf_cal_data = np.dtype([("ImageQualityFlag", "u1"),
                          ("ReferenceDataFlag", "u1"),
                          ("AbsCalMethod", "u1"),
                          ("Pad1", "u1"),
                          ("AbsCalWeightVic", ">f4"),
                          ("AbsCalWeightXsat", ">f4"),
                          ("AbsCalCoeff", ">f4"),
                          ("AbsCalError", ">f4"),
                          ("GSICSCalCoeff", ">f4"),
                          ("GSICSCalError", ">f4"),
                          ("GSICSOffsetCount", ">f4")])

radiometric_processing = np.dtype([('RPSummary',
                                    [('RadianceLinearization', 'bool', (12, )),
                                     ('DetectorEqualization', 'bool', (12, )),
                                     ('OnboardCalibrationResult', 'bool', (12, )),
                                     ('MPEFCalFeedback', 'bool', (12, )),
                                     ('MTFAdaptation', 'bool', (12, )),
                                     ('StraylightCorrectionFlag', 'bool', (12, ))]),
                                   ('Level1_5ImageCalibration',
                                    level_1_5_image_calibration, (12,)),
                                   ('BlackBodyDataUsed',
                                    [('BBObservationUTC', time_cds_expanded),
                                     ('BBRelatedData',
                                      [('OnBoardBBTime', cuc_time),
                                       ('MDUOutGain', '>u2', (42, )),
                                       ('MDUCoarseGain', 'u1', (42, )),
                                       ('MDUFineGain', '>u2', (42, )),
                                       ('MDUNumericalOffset', '>u2', (42, )),
                                       ('PUGain', '>u2', (42, )),
                                       ('PUOffset', '>u2', (27, )),
                                       ('PUBias', '>u2', (15, )),
                                       ('DCRValues', 'u1', (63, )),  # 42x12bits
                                       ('X_DeepSpaceWindowPosition', 'u1'),
                                       ('ColdFPTemperature',
                                        [('FCUNominalColdFocalPlaneTemp', '>u2'),
                                         ('FCURedundantColdFocalPlaneTemp', '>u2')]),
                                       ('WarmFPTemperature',
                                        [('FCUNominalWarmFocalPlaneVHROTemp', '>u2'),
                                         ('FCURedundantWarmFocalPlaneVHROTemp', '>u2')]),
                                       ('ScanMirrorTemperature',
                                        [('FCUNominalScanMirrorSensor1Temp', '>u2'),
                                         ('FCURedundantScanMirrorSensor1Temp', '>u2'),
                                         ('FCUNominalScanMirrorSensor2Temp', '>u2'),
                                         ('FCURedundantScanMirrorSensor2Temp', '>u2')]),
                                       ('M1M2M3Temperature',
                                        [('FCUNominalM1MirrorSensor1Temp', '>u2'),
                                         ('FCURedundantM1MirrorSensor1Temp', '>u2'),
                                         ('FCUNominalM1MirrorSensor2Temp', '>u2'),
                                         ('FCURedundantM1MirrorSensor2Temp', '>u2'),
                                         ('FCUNominalM23AssemblySensor1Temp', '>u1'),
                                         ('FCURedundantM23AssemblySensor1Temp', '>u1'),
                                         ('FCUNominalM23AssemblySensor2Temp', '>u1'),
                                         ('FCURedundantM23AssemblySensor2Temp', '>u1')]),
                                       ('BaffleTemperature',
                                        [('FCUNominalM1BaffleTemp', '>u2'),
                                         ('FCURedundantM1BaffleTemp', '>u2')]),
                                       ('BlackBodyTemperature',
                                        [('FCUNominalBlackBodySensorTemp', '>u2'),
                                         ('FCURedundantBlackBodySensorTemp', '>u2')]),
                                       ('FCUMode',
                                        [('FCUNominalSMMStatus', '>u2'),
                                         ('FCURedundantSMMStatus', '>u2')]),
                                       ('ExtractedBBData',
                                        [('NumberOfPixelsUsed', '>u4'),
                                         ('MeanCount', '>f4'),
                                         ('RMS', '>f4'),
                                         ('MaxCount', '>u2'),
                                         ('MinCount', '>u2'),
                                         ('BB_Processing_Slope', '>f8'),
                                         ('BB_Processing_Offset', '>f8')], (12,))])]),
                                   ('MPEFCalFeedback', impf_cal_data, (12, )),
                                   ('RadTransform', '>f4', (42, 64)),
                                   ('RadProcMTFAdaptation',
                                    [('VIS_IRMTFCorrectionE_W', '>f4', (33, 16)),
                                     ('VIS_IRMTFCorrectionN_S', '>f4', (33, 16)),
                                     ('HRVMTFCorrectionE_W', '>f4', (9, 16)),
                                     ('HRVMTFCorrectionN_S', '>f4', (9, 16)),
                                     ('StraylightCorrection', '>f4', (12, 8, 8))])])

geometric_processing = np.dtype([('OptAxisDistances',
                                  [('E-WFocalPlane', '>f4', (42, )),
                                   ('N-SFocalPlane', '>f4', (42, ))]),
                                 ('EarthModel',
                                  [('TypeOfEarthModel', 'u1'),
                                   ('EquatorialRadius', '>f8'),
                                   ('NorthPolarRadius', '>f8'),
                                   ('SouthPolarRadius', '>f8')]),
                                 ('AtmosphericModel', '>f4', (12, 360)),
                                 ('ResamplingFunctions', 'u1', (12, ))])

prologue = np.dtype([('SatelliteStatus', satellite_status),
                     ('ImageAcquisition', image_acquisition),
                     ('CelestialEvents', celestial_events),
                     ('ImageDescription', image_description),
                     ('RadiometricProcessing', radiometric_processing),
                     ('GeometricProcessing', geometric_processing)])

version = np.dtype([('Issue', '>u2'),
                    ('Revision', '>u2')])

impf_configuration = np.dtype([('OverallConfiguration', version),
                               ('SUDetails',
                                [('SUId', '>u2'),
                                 ('SUIdInstance', 'u1'),
                                 ('SUMode', 'u1'),
                                 ('SUState', 'u1'),
                                 ('SUConfiguration',
                                  [('SWVersion', version),
                                   ('InfoBaseVersions', version)])], (50, )),
                               ('WarmStartParms',
                                [('ScanningLaw', '>f8', (1527, )),
                                 ('RadFramesAlignment', '>f8', (3, )),
                                 ('ScanningLawVariation', '>f4', (2, )),
                                 ('EqualisationParms',
                                  [('ConstCoef', '>f4'),
                                   ('LinearCoef', '>f4'),
                                   ('QuadraticCoef', '>f4')], (42, )),
                                 ('BlackBodyDataForWarmStart',
                                  [('GTotalForMethod1', '>f8', (12, )),
                                   ('GTotalForMethod2', '>f8', (12, )),
                                   ('GTotalForMethod3', '>f8', (12, )),
                                   ('GBackForMethod1', '>f8', (12, )),
                                   ('GBackForMethod2', '>f8', (12, )),
                                   ('GBackForMethod3', '>f8', (12, )),
                                   ('RatioGTotalToGBack', '>f8', (12, )),
                                   ('GainInFrontOpticsCont', '>f8', (12, )),
                                   ('CalibrationConstants', '>f4', (12, )),
                                   ('MaxIncidentRadiance', '>f8', (12, )),
                                   ('TimeOfColdObsSeconds', '>f8'),
                                   ('TimeOfColdObsNanoSecs', '>f8'),
                                   ('IncidenceRadiance', '>f8', (12)),
                                   ('TempCal', '>f8'),
                                   ('TempM1', '>f8'),
                                   ('TempScan', '>f8'),
                                   ('TempM1Baf', '>f8'),
                                   ('TempCalSurround', '>f8')]),
                                 ('MirrorParameters',
                                  [('MaxFeedbackVoltage', '>f8'),
                                   ('MinFeedbackVoltage', '>f8'),
                                   ('MirrorSlipEstimate', '>f8')]),
                                 ('LastSpinPeriod', '>f8'),
                                 ('HKTMParameters',
                                  [('TimeS0Packet', time_cds_short),
                                   ('TimeS1Packet', time_cds_short),
                                   ('TimeS2Packet', time_cds_short),
                                   ('TimeS3Packet', time_cds_short),
                                   ('TimeS4Packet', time_cds_short),
                                   ('TimeS5Packet', time_cds_short),
                                   ('TimeS6Packet', time_cds_short),
                                   ('TimeS7Packet', time_cds_short),
                                   ('TimeS8Packet', time_cds_short),
                                   ('TimeS9Packet', time_cds_short),
                                   ('TimeSYPacket', time_cds_short),
                                   ('TimePSPacket', time_cds_short)]),
                                 ('WSPReserved', 'V3408')])])

# IMPFConfiguration_Record


def recarray2dict(arr):
    res = {}
    for dtuple in arr.dtype.descr:
        key = dtuple[0]
        ntype = dtuple[1]
        data = arr[key]
        if isinstance(ntype, list):
            res[key] = recarray2dict(data)
        else:
            res[key] = data

    return res


class HRITMSGPrologueFileHandler(HRITFileHandler):

    """MSG HRIT format reader
    """

    def __init__(self, filename, filename_info, filetype_info):
        """Initialize the reader."""
        super(HRITMSGPrologueFileHandler, self).__init__(filename, filename_info,
                                                         filetype_info,
                                                         (msg_hdr_map,
                                                          msg_variable_length_headers,
                                                          msg_text_headers))

        self.prologue = {}
        self.read_prologue()

    def read_prologue(self):
        """Read the prologue metadata."""
        tic = datetime.now()
        with open(self.filename, "rb") as fp_:
            fp_.seek(self.mda['total_header_length'])
            data = np.fromfile(fp_, dtype=prologue, count=1)[0]

            self.prologue.update(recarray2dict(data))

            try:
                impf = np.fromfile(fp_, dtype=impf_configuration, count=1)[0]
            except IndexError:
                logger.info('No IMPF configuration field found in prologue.')
            else:
                self.prologue.update(recarray2dict(impf))

        pacqtime = self.prologue['ImageAcquisition']['PlannedAcquisitionTime']

        start = pacqtime['TrueRepeatCycleStart']
        psend = pacqtime['PlannedForwardScanEnd']
        prend = pacqtime['PlannedRepeatCycleEnd']

        start = make_time_cds_expanded(start)
        psend = make_time_cds_expanded(psend)
        prend = make_time_cds_expanded(prend)

        pacqtime['TrueRepeatCycleStart'] = start
        pacqtime['PlannedForwardScanEnd'] = psend
        pacqtime['TrueRepeatCycleStart'] = prend

        logger.debug("Reading time " + str(datetime.now() - tic))

image_production_stats = np.dtype([('SatelliteId', '>u2'),
                                   ('ActualScanningSummary',
                                    [('NominalImageScanning', 'bool'),
                                     ('ReducedScan', 'bool'),
                                     ('ForwardScanStart', time_cds_short),
                                     ('ForwardScanEnd', time_cds_short)]),
                                   ('RadiometerBehaviour',
                                    [('NominalBehaviour', 'bool'),
                                     ('RadScanIrregularity', 'bool'),
                                        ('RadStoppage', 'bool'),
                                        ('RepeatCycleNotCompleted', 'bool'),
                                        ('GainChangeTookPlace', 'bool'),
                                        ('DecontaminationTookPlace', 'bool'),
                                        ('NoBBCalibrationAchieved', 'bool'),
                                        ('IncorrectTemperature', 'bool'),
                                        ('InvalidBBData', 'bool'),
                                        ('InvalidAuxOrHKTMData', 'bool'),
                                        ('RefocusingMechanismActuated', 'bool'),
                                        ('MirrorBackToReferencePos', 'bool')]),
                                   ('ReceptionSummaryStats',
                                    [('PlannedNumberOfL10Lines', '>u4', (12, )),
                                     ('NumberOfMissingL10Lines', '>u4', (12, )),
                                        ('NumberOfCorruptedL10Lines', '>u4', (12, )),
                                        ('NumberOfReplacedL10Lines', '>u4', (12, ))]),
                                   ('L15ImageValidity',
                                    [('NominalImage', 'bool'),
                                     ('NonNominalBecauseIncomplete', 'bool'),
                                        ('NonNominalRadiometricQuality', 'bool'),
                                        ('NonNominalGeometricQuality', 'bool'),
                                        ('NonNominalTimeliness', 'bool'),
                                        ('IncompleteL15', 'bool')]),
                                   ('ActualL15CoverageVIS_IR',
                                    [('SouthernLineActual', '>i4'),
                                     ('NorthernLineActual', '>i4'),
                                        ('EasternColumnActual', '>i4'),
                                        ('WesternColumnActual', '>i4')]),
                                   ('ActualL15CoverageHRV',
                                    [('LowerSouthLineActual', '>i4'),
                                     ('LowerNorthLineActual', '>i4'),
                                        ('LowerEastColumnActual', '>i4'),
                                        ('LowerWestColumnActual', '>i4'),
                                        ('UpperSouthLineActual', '>i4'),
                                        ('UpperNorthLineActual', '>i4'),
                                        ('UpperEastColumnActual', '>i4'),
                                        ('UpperWestColumnActual', '>i4')])])

epilogue = np.dtype([('15TRAILERVersion', 'u1'),
                     ('ImageProductionStats', image_production_stats)])
# FIXME: Add rest of the epilogue


class HRITMSGEpilogueFileHandler(HRITFileHandler):

    """MSG HRIT format reader
    """

    def __init__(self, filename, filename_info, filetype_info):
        """Initialize the reader."""
        super(HRITMSGEpilogueFileHandler, self).__init__(filename, filename_info,
                                                         filetype_info,
                                                         (msg_hdr_map,
                                                          msg_variable_length_headers,
                                                          msg_text_headers))
        self.epilogue = {}
        self.read_epilogue()

    def read_epilogue(self):
        """Read the prologue metadata."""
        tic = datetime.now()
        with open(self.filename, "rb") as fp_:
            fp_.seek(self.mda['total_header_length'])
            data = np.fromfile(fp_, dtype=epilogue, count=1)[0]

            self.epilogue.update(recarray2dict(data))

        pacqtime = self.epilogue['ImageProductionStats'][
            'ActualScanningSummary']

        start = pacqtime['ForwardScanStart']
        end = pacqtime['ForwardScanEnd']

        start = make_time_cds_short(start)
        end = make_time_cds_short(end)

        pacqtime['ForwardScanEnd'] = end
        pacqtime['ForwardScanStart'] = start


class HRITMSGFileHandler(HRITFileHandler):

    """MSG HRIT format reader
    """

    def __init__(self, filename, filename_info, filetype_info,
                 prologue, epilogue):
        """Initialize the reader."""
        super(HRITMSGFileHandler, self).__init__(filename, filename_info,
                                                 filetype_info,
                                                 (msg_hdr_map,
                                                  msg_variable_length_headers,
                                                  msg_text_headers))
        self.prologue = prologue.prologue
        self.epilogue = epilogue.epilogue
        earth_model = self.prologue['GeometricProcessing']['EarthModel']
        b = (earth_model['NorthPolarRadius'] +
             earth_model['SouthPolarRadius']) / 2.0 * 1000
        self.mda['projection_parameters'][
            'a'] = earth_model['EquatorialRadius'] * 1000
        self.mda['projection_parameters']['b'] = b
        ssp = self.prologue['ImageDescription'][
            'ProjectionDescription']['LongitudeOfSSP']
        self.mda['projection_parameters']['SSP_longitude'] = ssp

    @property
    def start_time(self):
        pacqtime = self.epilogue['ImageProductionStats'][
            'ActualScanningSummary']

        return pacqtime['ForwardScanStart']

    @property
    def end_time(self):
        pacqtime = self.epilogue['ImageProductionStats'][
            'ActualScanningSummary']

        return pacqtime['ForwardScanEnd']

    def calibrate(self, data, calibration):
        """Calibrate the data."""
        tic = datetime.now()

        if calibration == 'counts':
            return

        if calibration in ['radiance', 'reflectance', 'brightness_temperature']:
            self.convert_to_radiance(data)
        if calibration == 'reflectance':
            self._vis_calibrate(data)
        elif calibration == 'brightness_temperature':
            self._ir_calibrate(data)

        logger.debug("Calibration time " + str(datetime.now() - tic))

    def convert_to_radiance(self, data):
        """Calibrate to radiance.
        """

        gain = self._header["block5"]["gain_count2rad_conversion"][0]
        offset = self._header["block5"]["offset_count2rad_conversion"][0]

        data.data[:] *= gain
        data.data[:] += offset

    def _vis_calibrate(self, data):
        """Visible channel calibration only.
        """
        coeff = self._header["calibration"]["coeff_rad2albedo_conversion"]
        data.data[:] *= coeff * 100
        data.mask[data.data < 0] = True

    def _ir_calibrate(self, data):
        """IR calibration
        """

        cwl = self._header['block5']["central_wave_length"][0] * 1e-6
        c__ = self._header['calibration']["speed_of_light"][0]
        h__ = self._header['calibration']["planck_constant"][0]
        k__ = self._header['calibration']["boltzmann_constant"][0]
        a__ = (h__ * c__) / (k__ * cwl)

        # b__ = ((2 * h__ * c__ ** 2) / (1.0e6 * cwl ** 5 * data.data)) + 1

        data.data[:] *= 1.0e6 * cwl ** 5
        data.data[:] **= -1
        data.data[:] *= (2 * h__ * c__ ** 2)
        data.data[:] += 1

        # Te_ = a__ / np.log(b__)

        data.data[:] = a__ / np.log(data.data)

        c0_ = self._header['calibration']["c0_rad2tb_conversion"][0]
        c1_ = self._header['calibration']["c1_rad2tb_conversion"][0]
        c2_ = self._header['calibration']["c2_rad2tb_conversion"][0]

        # data.data[:] = c0_ + c1_ * Te_ + c2_ * Te_ ** 2

        data.data[:] = np.polyval([c2_, c1_, c0_], data.data)

        data.mask[data.data < 0] = True
        data.mask[np.isnan(data.data)] = True


def show(data, negate=False):
    """Show the stretched data.
    """
    from PIL import Image as pil
    data = np.array((data - data.min()) * 255.0 /
                    (data.max() - data.min()), np.uint8)
    if negate:
        data = 255 - data
    img = pil.fromarray(data)
    img.show()


if __name__ == "__main__":

    # TESTFILE = ("/media/My Passport/HIMAWARI-8/HISD/Hsfd/" +
    #            "201502/07/201502070200/00/B13/" +
    #            "HS_H08_20150207_0200_B13_FLDK_R20_S0101.DAT")
    TESTFILE = ("/local_disk/data/himawari8/testdata/" +
                "HS_H08_20130710_0300_B13_FLDK_R20_S1010.DAT")
    #"HS_H08_20130710_0300_B01_FLDK_R10_S1010.DAT")
    SCENE = ahisf([TESTFILE])
    SCENE.read_band(TESTFILE)
    SCENE.calibrate(['13'])
    # SCENE.calibrate(['13'], calibrate=0)

    # print SCENE._data['13']['counts'][0].shape

    show(SCENE.channels['13'], negate=False)

    import matplotlib.pyplot as plt
    plt.imshow(SCENE.channels['13'])
    plt.colorbar()
    plt.show()
