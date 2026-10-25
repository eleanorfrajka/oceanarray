Nortek Aquadopp Coordinate Transformation
==========================================

Nortek Aquadopp current profilers measure velocity along three acoustic beams.
The raw BEAM-frame data must be transformed to geographic East–North–Up (ENU)
coordinates before any oceanographic analysis.  This page documents the full
two-step transformation pipeline implemented in ``oceanarray``, the sign
conventions used, and why the specific choices were made.

The Nortek reference implementation used here is the `Nortek Support article
on manual coordinate system transformation
<https://support.nortekgroup.com/hc/en-us/articles/26048113914652-How-do-I-transform-a-coordinate-system-manually>`_,
and the companion Python script at ``docs/source/_static/code/3_beam_transformation.py``.


Overview
--------

The full transformation from BEAM to ENU proceeds in two steps:

.. math::

   \text{BEAM} \xrightarrow{\mathbf{T}} \text{XYZ} \xrightarrow{\mathbf{R}} \text{ENU}

where:

- :math:`\mathbf{T}` is the instrument-specific transformation matrix (BEAM→XYZ),
  stored in the instrument's ``.hdr`` file.
- :math:`\mathbf{R} = \mathbf{H} \cdot \mathbf{P}` is the orientation matrix
  (XYZ→ENU), built from the compass heading, pitch, and roll recorded at each
  timestep, plus the local magnetic declination.

In ``oceanarray``:

- **Stage 1** applies :math:`\mathbf{T}` and writes ``velocity_x``,
  ``velocity_y``, ``velocity_z`` alongside the retained beam velocities
  ``velocity_beam1/2/3``.
- **Stage 3** computes the magnetic declination via IGRF (``ppigrf``), builds
  :math:`\mathbf{R}` at every timestep, and writes ``east_velocity``,
  ``north_velocity``, ``up_velocity``.


Step 1 — BEAM → XYZ (:math:`\mathbf{T}`)
-----------------------------------------

The standard Nortek Aquadopp head has three beams arranged at 25° from
vertical (65° from the horizontal plane).  The transformation matrix
:math:`\mathbf{T}` maps beam velocities :math:`(V_1, V_2, V_3)` to the
instrument's Cartesian XYZ frame:

.. math::

   \begin{pmatrix} V_x \\ V_y \\ V_z \end{pmatrix}
   = \mathbf{T}
   \begin{pmatrix} V_1 \\ V_2 \\ V_3 \end{pmatrix}

For a standard Aquadopp head (integer-scaled form, divide by 4096):

.. math::

   \mathbf{T}_{\text{int}} =
   \begin{pmatrix}
    2896 &  2896 &    0 \\
   -2896 &  2896 &    0 \\
   -2896 & -2896 & 5792
   \end{pmatrix}

   \mathbf{T} = \mathbf{T}_{\text{int}} / 4096

This matrix is **instrument-specific**.  The values stored in the ``.hdr`` file
should always be preferred over the generic formula above.

**Pointing-down orientation**

When the instrument is mounted pointing downward (status bit 0 = 1), rows 2 and 3
(0-indexed: rows 1 and 2) of :math:`\mathbf{T}` change sign before application:

.. math::

   T[1, :] \leftarrow -T[1, :]
   \qquad
   T[2, :] \leftarrow -T[2, :]

This is controlled via the ``pointing_down: true`` key in the mooring YAML.
By default ``pointing_down`` is ``false`` (upward-looking).

**Reading the T matrix from instrument files**

Stage 1 parses :math:`\mathbf{T}` from the instrument header:

- **Old format** (``.hdr`` text block labelled ``"Transformation matrix"``) —
  handled by ``_parse_nortek_T_matrix_hdr()``.
- **New format** (``.hdr`` or ``String Data.csv`` with
  ``GETXFAVG,ROWS=3,COLS=3,M11=...,M33=...``) — handled by
  ``_parse_nortek_T_matrix_csv()``.

The nine elements are stored in the dataset's global attributes as
``nortek_T_M11`` … ``nortek_T_M33``, and XYZ velocities are written as
``velocity_x``, ``velocity_y``, ``velocity_z``.  The original beam velocities
(``velocity_beam1/2/3``) are retained for post-hoc verification.

If the header cannot be parsed, Stage 1 leaves ``nortek_coordinate_system``
as ``"BEAM"``.  Stage 3 will then skip the transformation entirely and log a
warning asking you to re-run Stage 1 with the correct header file.
There is no analytical fallback — an instrument-specific T matrix is required.


Step 2 — XYZ → ENU (:math:`\mathbf{R} = \mathbf{H} \cdot \mathbf{P}`)
-----------------------------------------------------------------------

The orientation matrix :math:`\mathbf{R}` converts instrument-frame XYZ
velocities to geographic ENU coordinates.  It is the product of a heading
matrix :math:`\mathbf{H}` and a tilt matrix :math:`\mathbf{P}`.

This step is performed in Stage 3 because it requires the local magnetic
declination, which depends on the deployment position and time.

Heading matrix :math:`\mathbf{H}`
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The Nortek convention defines *heading* as the compass bearing (clockwise from
geographic North) of the instrument's X-axis.  The heading matrix is:

.. math::

   \text{let } h = \text{heading} - 90° + \delta

   \mathbf{H} =
   \begin{pmatrix}
    \cos h &  \sin h & 0 \\
   -\sin h &  \cos h & 0 \\
    0      &  0      & 1
   \end{pmatrix}

where :math:`\delta` is the **magnetic declination** (positive East).  The
``-90°`` offset is an instrument-frame convention: at heading = 90° (X-axis
pointing East) with no tilt, :math:`h = 0°` and :math:`\mathbf{H} = \mathbf{I}`,
giving X → East, Y → North, Z → Up — the standard ENU alignment.

Magnetic declination is computed at the deployment midpoint using the IGRF
model (``ppigrf`` package):

.. code-block:: python

   Be, Bn, _ = ppigrf.igrf(lon, lat, 0.0, t_mid)
   declination = degrees(arctan2(Be, Bn))   # positive East

Tilt matrix :math:`\mathbf{P}`
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Pitch (:math:`p`) and roll (:math:`r`) are combined into a single tilt matrix:

.. math::

   \mathbf{P} =
   \begin{pmatrix}
    \cos p & -\sin p \sin r & -\cos r \sin p \\
    0      &  \cos r        & -\sin r        \\
    \sin p &  \sin r \cos p &  \cos p \cos r
   \end{pmatrix}

Combined rotation :math:`\mathbf{R}`
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Expanding :math:`\mathbf{R} = \mathbf{H} \cdot \mathbf{P}` with
:math:`c_h = \cos h,\; s_h = \sin h,\; c_p = \cos p,\; s_p = \sin p,\;
c_r = \cos r,\; s_r = \sin r`:

.. math::

   \begin{pmatrix} E \\ N \\ U \end{pmatrix}
   = \mathbf{R}
   \begin{pmatrix} V_x \\ V_y \\ V_z \end{pmatrix}

where:

.. math::

   E &= c_h c_p V_x + (-c_h s_p s_r + s_h c_r) V_y + (-c_h s_p c_r - s_h s_r) V_z \\
   N &= -s_h c_p V_x + ( s_h s_p s_r + c_h c_r) V_y + ( s_h s_p c_r - c_h s_r) V_z \\
   U &= s_p V_x + s_r c_p V_y + c_p c_r V_z

Sanity check (heading = 90°, no tilt):
:math:`h = 0°`, so :math:`c_h = 1,\; s_h = 0`, and
:math:`\mathbf{P} = \mathbf{I}`.  Then :math:`E = V_x`,
:math:`N = V_y`, :math:`U = V_z` — X → East, Y → North. ✓

Sanity check (heading = 0°, no tilt):
:math:`h = -90°`, so :math:`c_h = 0,\; s_h = -1`.  Then
:math:`E = -V_y`, :math:`N = V_x`, :math:`U = V_z` — X → North. ✓


Tilt QC
-------

Excessive instrument tilt degrades the velocity measurement.  The combined
tilt angle is computed from pitch :math:`p` and roll :math:`r`:

.. math::

   \theta_{\text{tilt}} = \arccos(\cos p \cdot \cos r)

QARTOD flags are applied by Stage 3:

- :math:`\theta_{\text{tilt}} > 20°` → flag 3 (suspect)
- :math:`\theta_{\text{tilt}} > 30°` → flag 4 (bad)

The tilt variable is also written to the per-instrument report so the flagging
threshold can be visually verified.


Current speed and direction
---------------------------

After the ENU transform, current speed and direction are derived from the
horizontal components:

.. math::

   \text{speed} &= \sqrt{E^2 + N^2} \\
   \text{direction} &= \operatorname{atan2}(E, N) \bmod 360°

Direction follows oceanographic convention (the direction *toward* which the
current flows, measured clockwise from North).


Implementation reference
------------------------

The reference Python script from Nortek Support is reproduced at
``docs/source/_static/code/3_beam_transformation.py``.  Key excerpts:

.. code-block:: python

   # Heading adjustment: -90° because heading=90° aligns X with East
   hdg = np.radians(heading - 90)

   H = np.array([
       [ np.cos(hdg),  np.sin(hdg), 0],
       [-np.sin(hdg),  np.cos(hdg), 0],
       [           0,            0, 1]
   ])

   P = np.array([
       [ np.cos(pch), -np.sin(pch)*np.sin(rll), -np.cos(rll)*np.sin(pch)],
       [           0,              np.cos(rll),             -np.sin(rll)],
       [ np.sin(pch),  np.sin(rll)*np.cos(pch),  np.cos(pch)*np.cos(rll)]
   ])

   R = H @ P

The ``oceanarray`` implementation in ``pipeline.coordinate.xyz_to_enu()`` expands this
product analytically (vectorised over the time dimension) and adds the magnetic
declination offset to the heading before computing :math:`h`.


Configuration reference
-----------------------

Relevant YAML keys under each Nortek instrument entry:

.. list-table::
   :header-rows: 1
   :widths: 20 12 60

   * - Key
     - Default
     - Description
   * - ``pointing_down``
     - ``false``
     - Set to ``true`` if the head points downward.  Negates rows 1 and 2 of
       :math:`\mathbf{T}` before the BEAM→XYZ transform.
   * - ``header``
     - —
     - Path (relative to the instrument folder) of the ``.hdr`` file or
       ``String Data.csv`` from which the T matrix is extracted.

The magnetic declination is computed automatically from the mooring latitude,
longitude, and deployment midpoint using the IGRF model; no manual entry is
required.


See also
--------

- `Nortek Support: How do I transform a coordinate system manually?
  <https://support.nortekgroup.com/hc/en-us/articles/26048113914652-How-do-I-transform-a-coordinate-system-manually>`_
- :doc:`auto_qc` — QARTOD flags applied to velocity after tilt QC
- :py:func:`oceanarray.pipeline.coordinate.xyz_to_enu` — vectorised ENU rotation
- :py:func:`oceanarray.pipeline.coordinate.apply_beam_to_enu` — full BEAM/XYZ → ENU pipeline
- :py:func:`oceanarray.pipeline.stage1._parse_nortek_T_matrix_hdr` — header parsing
