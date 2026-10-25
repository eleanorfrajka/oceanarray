
Calibration dips or "caldips"
=============================

To identify biases in microCAT data, we use calibration dips where microCAT instruments are attached to the CTD rosette and then sent on a CTD cast.  During the upcast, instead of normal 30-second bottle stops, the bottle stops are extended to 5 or 10 minutes, during which time the microCATs are sampling at 10 second intervals, while the shipboard CTD 911 is sampling at 24 Hz.  Roughly the last 3 minutes of the bottle stop are used to perform a comparison between the microCAT- and 911- measured conductivity, temperature and (if available) pressure.

This is an intermediate step in the instrument level processing.  Normally the calibration dip (caldip) will be performed prior to deployment and after recovery.  Pre-deployment casts can be used to exclude instruments from deployment (e.g., if measured values differ by more than prescribed tolerances: 0.05 for conductivity, 0.005 for temperature and 5 dbar for pressure).  Differences between the values on the deployment cast and recovery cast can be used to adjust the microCAT data, e.g., by applying a linear change in temperature between the pre- and post-deployment caldips.



Calibration dip: Procedure
---------------------------

The microCATs are set up to start before or during the downcast of the CTD, at a 10 second sampling interval.  After recovery, the microCATs are stopped and then data are downloaded.

"Water impact" times are determined for clock synchronisation between the microCAT and CTD, but only if the microCAT is sampling before the start of the cast.  Bottle files are used to determine the bottle stop timing.  Data are extracted from the final 3 minutes of the bottle stop by first identifying the bottle stop, averaging the CTD 911 pressure from the first 1 minute of the bottle stop, and then using the section of the profile when the measured CTD 911 pressure is within 5m of the bottle stop pressure.  This window should be roughly 5 minutes long.  The final 30 seconds can be removed and then 3 minutes before the final 30 seconds identified as a time window for comparison between the 911 CTD and the microCAT data.

Within this 3 minute window for EACH bottle stop, determine the offset (dt) between the microCAT and CTD temperature, (dp) pressure and (dc) conductivity.  Also determine the 911 CTD standard deviation of temperature T, pressure P and conductivity C, and the standard deviation for each microCAT.

For microCATs without pressure, use the CTD pressure to correct the conductivity.

Calibration dip processing is handled by the `caldip <https://github.com/ocean-uhh/caldip>`_ package, which implements this workflow in Python.  Install it with ``pip install git+https://github.com/ocean-uhh/caldip.git``.  oceanarray consumes its output as an optional correction at the front of Stage 3 — caldip is not a separate stage; see :doc:`methods/calibration`.  The ``caldip`` package provides functions for water impact detection, bottle stop identification, offset calculation, and diagnostic plots.

While running the processing, a figure is produced which allows for zooming in.  The figure should show in the top panel: CTD 911 pressure (black and thick), and then microCAT pressure (thin, colored, with a legend that identifies the microCAT serial number).  In the middle panel: CTD 911 temperature and microCAT temperature, and in the bottom panel: CTD 911 conductivity and microCAT conductivity.
