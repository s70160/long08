from cereal import car
from selfdrive.car.hyundai.values import DBC, STEER_THRESHOLD, FEATURES
from selfdrive.car.interfaces import CarStateBase
from opendbc.can.parser import CANParser
from selfdrive.config import Conversions as CV

GearShifter = car.CarState.GearShifter


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.cruise_main_button = 0

  def update(self, cp, cp_cam):
    ret = car.CarState.new_message()

    ret.doorOpen = any([cp.vl["CGW1"]['CF_Gway_DrvDrSw'], cp.vl["CGW1"]['CF_Gway_AstDrSw'],
                        cp.vl["CGW2"]['CF_Gway_RLDrSw'], cp.vl["CGW2"]['CF_Gway_RRDrSw']])

    ret.seatbeltUnlatched = cp.vl["CGW1"]['CF_Gway_DrvSeatBeltSw'] == 0

    ret.wheelSpeeds.fl = cp.vl["WHL_SPD11"]['WHL_SPD_FL'] * CV.KPH_TO_MS
    ret.wheelSpeeds.fr = cp.vl["WHL_SPD11"]['WHL_SPD_FR'] * CV.KPH_TO_MS
    ret.wheelSpeeds.rl = cp.vl["WHL_SPD11"]['WHL_SPD_RL'] * CV.KPH_TO_MS
    ret.wheelSpeeds.rr = cp.vl["WHL_SPD11"]['WHL_SPD_RR'] * CV.KPH_TO_MS
    ret.vEgoRaw = (ret.wheelSpeeds.fl + ret.wheelSpeeds.fr + ret.wheelSpeeds.rl + ret.wheelSpeeds.rr) / 4.
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)

    ret.standstill = ret.vEgoRaw < 0.1
    self.prev_cruise_buttons = self.cruise_buttons
    self.prev_cruise_main_button = self.cruise_main_button
    ret.steeringAngle = cp.vl["SAS11"]['SAS_Angle']
    ret.steeringRate = cp.vl["SAS11"]['SAS_Speed']
    ret.yawRate = cp.vl["ESP12"]['YAW_RATE']
    ret.leftBlinker = cp.vl["CGW1"]['CF_Gway_TSigLHSw'] != 0
    ret.rightBlinker = cp.vl["CGW1"]['CF_Gway_TSigRHSw'] != 0
    ret.steeringTorque = cp.vl["MDPS12"]['CR_Mdps_StrColTq']
    ret.steeringTorqueEps = cp.vl["MDPS12"]['CR_Mdps_OutTq']
    ret.steeringPressed = abs(ret.steeringTorque) > STEER_THRESHOLD
    ret.steerWarning = cp.vl["MDPS12"]['CF_Mdps_ToiUnavail'] != 0

    # cruise state
    #self.main_on = (cp_scc.vl["SCC11"]["MainMode_ACC"] != 0) if not self.no_radar else \
    #                                    cp.vl['EMS16']['CRUISE_LAMP_M']
    #self.acc_active = (cp_scc.vl["SCC12"]['ACCMode'] != 0) if not self.no_radar else \
    #                                  (cp.vl["LVR12"]['CF_Lvr_CruiseSet'] != 0)
    ret.cruiseState.enabled = cp.vl["LVR12"]['CF_Lvr_CruiseSet'] != 0
    ret.cruiseState.available = True
    ret.cruiseState.standstill = False
    self.is_set_speed_in_mph = int(cp.vl["CLU11"]["CF_Clu_SPEED_UNIT"])
    if ret.cruiseState.enabled:
      speed_conv = CV.MPH_TO_MS if self.is_set_speed_in_mph else CV.KPH_TO_MS
      ret.cruiseState.speed = cp.vl["LVR12"]["CF_Lvr_CruiseSet"] * speed_conv
    else:
      ret.cruiseState.speed = 0
    self.cruise_main_button = cp.vl["CLU11"]["CF_Clu_CruiseSwMain"]
    self.cruise_buttons = cp.vl["CLU11"]["CF_Clu_CruiseSwState"]

    # TODO: Find brake pressure
    ret.brake = 0
    ret.brakePressed = cp.vl["TCS13"]['DriverBraking'] != 0

    # TODO: Check this
    ret.brakeLights = bool(cp.vl["TCS13"]['BrakeLight'] or ret.brakePressed)

    #TODO: find pedal signal for EV/HYBRID Cars
    ret.gas = cp.vl["EMS12"]['PV_AV_CAN'] / 100
    ret.gasPressed = bool(cp.vl["EMS16"]["CF_Ems_AclAct"])

    # TODO: refactor gear parsing in function
    # Gear Selection via Cluster - For those Kia/Hyundai which are not fully discovered, we can use the Cluster Indicator for Gear Selection,
    # as this seems to be standard over all cars, but is not the preferred method.
    if self.CP.carFingerprint in FEATURES["use_cluster_gears"]:
      if cp.vl["CLU15"]["CF_Clu_InhibitD"] == 1:
        ret.gearShifter = GearShifter.drive
      elif cp.vl["CLU15"]["CF_Clu_InhibitN"] == 1:
        ret.gearShifter = GearShifter.neutral
      elif cp.vl["CLU15"]["CF_Clu_InhibitP"] == 1:
        ret.gearShifter = GearShifter.park
      elif cp.vl["CLU15"]["CF_Clu_InhibitR"] == 1:
        ret.gearShifter = GearShifter.reverse
      else:
        ret.gearShifter = GearShifter.unknown
    # Gear Selecton via TCU12
    elif self.CP.carFingerprint in FEATURES["use_tcu_gears"]:
      gear = cp.vl["TCU12"]["CUR_GR"]
      if gear == 0:
        ret.gearShifter = GearShifter.park
      elif gear == 14:
        ret.gearShifter = GearShifter.reverse
      elif gear > 0 and gear < 9:    # unaware of anything over 8 currently
        ret.gearShifter = GearShifter.drive
      else:
        ret.gearShifter = GearShifter.unknown
    # Gear Selecton - This is only compatible with optima hybrid 2017
    elif self.CP.carFingerprint in FEATURES["use_elect_gears"]:
      gear = cp.vl["ELECT_GEAR"]["Elect_Gear_Shifter"]
      if gear in (5, 8):  # 5: D, 8: sport mode
        ret.gearShifter = GearShifter.drive
      elif gear == 6:
        ret.gearShifter = GearShifter.neutral
      elif gear == 0:
        ret.gearShifter = GearShifter.park
      elif gear == 7:
        ret.gearShifter = GearShifter.reverse
      else:
        ret.gearShifter = GearShifter.unknown
    # Gear Selecton - This is not compatible with all Kia/Hyundai's, But is the best way for those it is compatible with
    else:
      gear = cp.vl["LVR12"]["CF_Lvr_Gear"]
      if gear in (5, 8):  # 5: D, 8: sport mode
        ret.gearShifter = GearShifter.drive
      elif gear == 6:
        ret.gearShifter = GearShifter.neutral
      elif gear == 0:
        ret.gearShifter = GearShifter.park
      elif gear == 7:
        ret.gearShifter = GearShifter.reverse
      else:
        ret.gearShifter = GearShifter.unknown

    # save the entire LKAS11 and CLU11
    self.lkas11 = cp_cam.vl["LKAS11"]
    self.clu11 = cp.vl["CLU11"]
    self.park_brake = cp.vl["CGW1"]['CF_Gway_ParkBrakeSw']
    self.steer_state = cp.vl["MDPS12"]['CF_Mdps_ToiActive'] #0 NOT ACTIVE, 1 ACTIVE
    self.lead_distance = 20

    return ret

  @staticmethod
  def get_can_parser(CP):
    signals = [
      # sig_name, sig_address, default
      ("WHL_SPD_FL", "WHL_SPD11", 0),
      ("WHL_SPD_FR", "WHL_SPD11", 0),
      ("WHL_SPD_RL", "WHL_SPD11", 0),
      ("WHL_SPD_RR", "WHL_SPD11", 0),

      ("YAW_RATE", "ESP12", 0),

      ("CF_Gway_DrvSeatBeltInd", "CGW4", 1),

      ("CF_Gway_DrvSeatBeltSw", "CGW1", 0),
      ("CF_Gway_DrvDrSw", "CGW1", 0),       # Driver Door
      ("CF_Gway_AstDrSw", "CGW1", 0),       # Passenger door
      ("CF_Gway_RLDrSw", "CGW2", 0),        # Rear reft door
      ("CF_Gway_RRDrSw", "CGW2", 0),        # Rear right door
      ("CF_Gway_TSigLHSw", "CGW1", 0),
      ("CF_Gway_TurnSigLh", "CGW1", 0),
      ("CF_Gway_TSigRHSw", "CGW1", 0),
      ("CF_Gway_TurnSigRh", "CGW1", 0),
      ("CF_Gway_ParkBrakeSw", "CGW1", 0),

      ("CYL_PRES", "ESP12", 0),

      ("CF_Clu_CruiseSwState", "CLU11", 0),
      ("CF_Clu_CruiseSwMain", "CLU11", 0),
      ("CF_Clu_SldMainSW", "CLU11", 0),
      ("CF_Clu_ParityBit1", "CLU11", 0),
      ("CF_Clu_VanzDecimal" , "CLU11", 0),
      ("CF_Clu_Vanz", "CLU11", 0),
      ("CF_Clu_SPEED_UNIT", "CLU11", 0),
      ("CF_Clu_DetentOut", "CLU11", 0),
      ("CF_Clu_RheostatLevel", "CLU11", 0),
      ("CF_Clu_CluInfo", "CLU11", 0),
      ("CF_Clu_AmpInfo", "CLU11", 0),
      ("CF_Clu_AliveCnt1", "CLU11", 0),

      ("ACCEnable", "TCS13", 0),
      ("BrakeLight", "TCS13", 0),
      ("DriverBraking", "TCS13", 0),

      ("ESC_Off_Step", "TCS15", 0),

      ("CF_Lvr_GearInf", "LVR11", 0),        # Transmission Gear (0 = N or P, 1-8 = Fwd, 14 = Rev)

      ("CR_Mdps_StrColTq", "MDPS12", 0),
      ("CF_Mdps_ToiActive", "MDPS12", 0),
      ("CF_Mdps_ToiUnavail", "MDPS12", 0),
      ("CF_Mdps_FailStat", "MDPS12", 0),
      ("CR_Mdps_OutTq", "MDPS12", 0),

      ("SAS_Angle", "SAS11", 0),
      ("SAS_Speed", "SAS11", 0),

      ("PV_AV_CAN", "EMS12", 0),
      ("CF_Ems_AclAct", "EMS16", 0),
    ]

    checks = [
      # address, frequency
      ("MDPS12", 50),
      ("TCS13", 50),
      ("TCS15", 10),
      ("CLU11", 50),
      ("ESP12", 100),
      ("CGW1", 10),
      ("CGW4", 5),
      ("WHL_SPD11", 50),
      ("SAS11", 100),
      ("EMS12", 100),
      ("EMS16", 100),
    ]

    signals += [
      ("CRUISE_LAMP_M", "EMS16", 0),
      ("CF_Lvr_CruiseSet", "LVR12", 0),
      # ("Vision_ObjDist_Low","V_OptData_739", 0),
    ]

    if CP.carFingerprint in FEATURES["use_cluster_gears"]:
      signals += [
        ("CF_Clu_InhibitD", "CLU15", 0),
        ("CF_Clu_InhibitP", "CLU15", 0),
        ("CF_Clu_InhibitN", "CLU15", 0),
        ("CF_Clu_InhibitR", "CLU15", 0),
      ]
      checks += [
        ("CLU15", 5)
      ]
    elif CP.carFingerprint in FEATURES["use_tcu_gears"]:
      signals += [
        ("CUR_GR", "TCU12", 0)
      ]
      checks += [
        ("TCU12", 100)
      ]
    elif CP.carFingerprint in FEATURES["use_elect_gears"]:
      signals += [("Elect_Gear_Shifter", "ELECT_GEAR", 0)]
      checks += [("ELECT_GEAR", 20)]
    else:
      signals += [
        ("CF_Lvr_Gear", "LVR12", 0)
      ]
      checks += [
        ("LVR12", 100)
      ]

    return CANParser(DBC[CP.carFingerprint]['pt'], signals, checks, 0)

  @staticmethod
  def get_cam_can_parser(CP):

    signals = [
      # sig_name, sig_address, default
      ("CF_Lkas_Bca_R", "LKAS11", 0),
      ("CF_Lkas_LdwsSysState", "LKAS11", 0),
      ("CF_Lkas_SysWarning", "LKAS11", 0),
      ("CF_Lkas_LdwsLHWarning", "LKAS11", 0),
      ("CF_Lkas_LdwsRHWarning", "LKAS11", 0),
      ("CF_Lkas_HbaLamp", "LKAS11", 0),
      ("CF_Lkas_FcwBasReq", "LKAS11", 0),
      ("CF_Lkas_HbaSysState", "LKAS11", 0),
      ("CF_Lkas_FcwOpt", "LKAS11", 0),
      ("CF_Lkas_HbaOpt", "LKAS11", 0),
      ("CF_Lkas_FcwSysState", "LKAS11", 0),
      ("CF_Lkas_FcwCollisionWarning", "LKAS11", 0),
      ("CF_Lkas_FusionState", "LKAS11", 0),
      ("CF_Lkas_FcwOpt_USM", "LKAS11", 0),
      ("CF_Lkas_LdwsOpt_USM", "LKAS11", 0)
    ]

    checks = []

    return CANParser(DBC[CP.carFingerprint]['pt'], signals, checks, 2)
