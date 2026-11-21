Calibration dips
================

A calibration dip (*caldip*) checks moored instruments against the ship's CTD.
The instruments are strapped to the CTD frame and lowered on a cast, before
deployment and again after recovery. On the way up the CTD stops at several
depths for 5–10 minutes, longer than a usual bottle stop, so the instruments
(sampling every 10 s) and the CTD (24 Hz) settle at the same water.

.. figure:: /_static/caldip_cast.png
   :alt: Pressure of the CTD and the moored instruments during a calibration-dip cast
   :align: center

   A caldip cast (cast M4, MSM142): the CTD's pressure (black) and every
   instrument's, down to 2000 dbar and back up with five stops. The vertical lines
   mark each stop (solid) and the stretch compared (dotted).

How a stop is compared
----------------------

1. Find the stop from the bottle file: the CTD pressure stays within 5 dbar of the
   stop's pressure (the mean of its first minute).
2. Drop the last 30 s and keep the 3 minutes before them.
3. In that window, take each instrument's difference from the CTD in temperature,
   conductivity and (if it has a sensor) pressure, and the scatter of both. An
   instrument without a pressure sensor uses the CTD's pressure for its
   conductivity.

The clocks are matched first by the moment the package hits the water, when the
instruments were already sampling at the start of the cast.

What it is used for
-------------------

- **Before deployment** — an instrument that differs from the CTD by more than
  0.005 °C, 0.05 mS cm⁻¹ or 5 dbar is not deployed.
- **Before and after** — the differences at the two dips give each instrument's
  offset and its drift over the deployment, for example a linear change in
  temperature between them.

The comparison is done by the `caldip <https://github.com/ocean-uhh/caldip>`_
package (``pip install git+https://github.com/ocean-uhh/caldip.git``), which finds
the water impact and the stops, computes the offsets and draws the figures.
oceanarray will apply its offsets at the start of stage 3; that step is designed
but not applied yet (:doc:`methods/calibration`).
