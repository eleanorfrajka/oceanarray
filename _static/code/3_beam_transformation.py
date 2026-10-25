# -*- coding: utf-8 -*-
"""Created Mar 2025

@author: Nortek Support
"""

import numpy as np

# This is a script that shows how velocity data can be
# transformed between BEAM coordinates and ENU coordinates. BEAM
# coordinates are defined as the velocity measured along the three
# beams of the instrument.
# ENU coordinates are defined in an earth coordinate system, where
# E represents the East-West component, N represents the North-South
# component and U represents the Up-Down component.


# ------------------------------------------------------------
# Transformation matrix T for BEAM to XYZ coordinates:
# ------------------------------------------------------------


# This example shows the transformation matrix for a standard Aquadopp head

T = np.array([[2896, 2896, 0], [-2896, 2896, 0], [-2896, -2896, 5792]], dtype=float)


# If necessary, scale the transformation matrix to floating point values
T /= 4096.0

# Store original matrix
T_org = T.copy()

# If instrument is pointing down (bit 0 in status equal to 1)
# rows 2 and 3 must change sign
# NOTE: For the Vector the instrument is defined to be in
#       the UP orientation when the communication cable
#       end of the canister is on the top of the canister, ie when
#       the probe is pointing down.
#       For the other instruments that are used vertically, the UP
#       orientation is defined to be when the head is on the upper
#       side of the canister.

statusbit0 = 1

if statusbit0 == 1:
    T[1, :] = -T[1, :]
    T[2, :] = -T[2, :]

# ------------------------------------------------------------
# Transformation matrix R for XYZ to ENU coordinates:
# ------------------------------------------------------------

# Note that the transformation matrix R must be recalculated every time
# the orientation, heading, pitch or roll changes.

# Define heading, pitch, and roll in degrees
# Replace these with real data from your ADCP
heading = 120  # example value in degrees
pitch = 5  # example value in degrees
roll = -2  # example value in degrees

# Convert to radians
hdg = np.radians(heading - 90)
# Adjust heading by -90 degrees due to orientation of x
pch = np.radians(pitch)
rll = np.radians(roll)

# Heading matrix
H = np.array([[np.cos(hdg), np.sin(hdg), 0], [-np.sin(hdg), np.cos(hdg), 0], [0, 0, 1]])

# Tilt matrix (Pitch and Roll combined)
P = np.array(
    [
        [np.cos(pch), -np.sin(pch) * np.sin(rll), -np.cos(rll) * np.sin(pch)],
        [0, np.cos(rll), -np.sin(rll)],
        [np.sin(pch), np.sin(rll) * np.cos(pch), np.cos(pch) * np.cos(rll)],
    ]
)

# Final transformation matrix from XYZ to ENU
R = H @ P

# combine with T
F = R @ T

# -----------------------------------------------
# -------- APPLY TRANSFORMATION MATRICES --------
# -----------------------------------------------

# Example beam velocity vector
beam = np.array([0.23, -0.52, 0.12])

# BEAM → ENU
enu = F @ beam

# ENU → BEAM
beam_from_enu = np.linalg.inv(F) @ enu


# BEAM → XYZ
xyz = T_org @ beam

# XYZ → BEAM
beam_from_xyz = np.linalg.inv(T_org) @ xyz


# XYZ → ENU
enu_from_xyz = F @ np.linalg.inv(T_org) @ xyz


# ENU → XYZ
xyz_from_enu = T_org @ np.linalg.inv(F) @ enu
