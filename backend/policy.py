"""Which features a web user may see and change.

The deny lists protect the network and the unit's identity, not the image: a
stolen password must not be able to re-address the camera, aim its video stream
at another host, unlock factory access or alter identity records.
"""


class Locked(Exception):
    pass


# Never sent to the browser.
HIDDEN = {
    "FeatureAccessPassword", "FeatureAccessLock", "FactoryAccessUnlocked", "FactoryUserSetSave",
    "DeviceProvisioningRecord", "TLParamsLocked", "AcquisitionStart", "AcquisitionStop",
    "GevSCPSFireTestPacket", "GevInterfaceSelector", "GevStreamChannelSelector",
}

# Snapshot commands/selectors are private to the atomic AI transaction. Exposing
# them individually in All settings could leave a latch held or mix objects.
AI_SETTINGS = {"MaxAIEnabledFeatures", "MaxAISamplePeriodUs", "MaxAIMinimumScore", "MaxAIFaceCosineThreshold"}
HIDDEN.update({"MaxAIResultLatch", "MaxAIResultRelease", "MaxAIResultValid", "MaxAIObjectCount",
               "MaxAIResultSequence", "MaxAICaptureSequence", "MaxAITimestampLow", "MaxAITimestampHigh",
               "MaxAIObjectSelector", "MaxAIResultWidth", "MaxAIResultHeight", "MaxAIResultPixelFormat",
               "MaxAIResultConfigGeneration", "MaxAIResultAgeUs"})
HIDDEN.update("MaxAIObject" + suffix for suffix in
              ["Feature", "Class", "Score", "X", "Y", "Width", "Height", "TrackID"] +
              [f"TextWord{i}" for i in range(8)])

# Shown as read-only values.
READ_ONLY = {
    "GevSCDA", "GevSCPHostPort", "GevSCSP", "GevSCPSPacketSize", "GevSCPSDoNotFragment", "GevHeartbeatTimeout",
    "DeviceLinkHeartbeatTimeout", "GevPersistentIPAddress", "GevPersistentSubnetMask",
    "GevPersistentDefaultGateway", "GevCurrentIPConfigurationPersistentIP",
    "GevCurrentIPConfigurationDHCP", "GevCurrentIPConfigurationLLA", "GevCurrentIPAddress",
    "GevCurrentSubnetMask", "GevCurrentDefaultGateway", "DeviceUserID", "AcquisitionMode",
}

# Lasting or disruptive actions; the interface asks before running them.
CONFIRM = {
    "UserSetSave": "Overwrite the selected preset in the camera's flash memory?",
    "UserSetDefault": "Change which preset the camera loads when it powers up?",
    "DeviceReset": "Reset the camera? It drops every connection and reloads its power-up preset.",
}


def writable(name):
    return name not in HIDDEN and name not in READ_ONLY
