.. This is an internal planning document, not a user-facing page.

Rotary cross-spectrum for grid report — design plan
=====================================================

**Status**: design only — not yet implemented.

Background
----------

A rotary spectrum decomposes horizontal velocity into:

- **Counter-clockwise (CCW)** component: associated with positive frequencies
- **Clockwise (CW)** component: associated with negative frequencies

For a complex velocity signal :math:`w(t) = u(t) + i\,v(t)`, the cross-spectrum
between :math:`u` and :math:`v` yields the rotary decomposition at each frequency
:math:`f`:

.. math::

   S_{CW}(f)  &= \tfrac{1}{4}\bigl[S_{uu}(f) + S_{vv}(f) + 2\,\mathrm{Im}(S_{uv}(f))\bigr] \\
   S_{CCW}(f) &= \tfrac{1}{4}\bigl[S_{uu}(f) + S_{vv}(f) - 2\,\mathrm{Im}(S_{uv}(f))\bigr]

The **rotary coefficient** indicates the preferred rotation sense:

.. math::

   r(f) = \frac{S_{CCW}(f) - S_{CW}(f)}{S_{CCW}(f) + S_{CW}(f)} \in [-1, +1]

- :math:`r > 0`: predominantly CCW (typical of Northern Hemisphere near-inertial and tidal waves)
- :math:`r < 0`: predominantly CW

The total velocity variance is preserved: :math:`S_{CW} + S_{CCW} = (S_{uu} + S_{vv})/2`.

This analysis is most meaningful for currents where tidal or near-inertial energy is
expected to dominate.  It is standard in mooring data analysis (Gonella 1972; Mooers 1973).

What to add to the grid report
--------------------------------

A new section **"Rotary spectrum"** rendered when both ``east_velocity`` and
``north_velocity`` are present in the grid dataset.

Suggested three-panel figure (13 × 8 inches):

- **Left panel** — CW power spectrum vs period (days), one line per selected depth level,
  coloured by pressure shallow→deep (``Blues_r``).  Log–log axes.
- **Centre panel** — CCW power spectrum vs period (days), same colour scheme and axes.
- **Right panel** — Rotary coefficient :math:`r(f)` vs period (days), same colour scheme.
  Linear y-axis bounded to :math:`[-1, 1]`; x-axis log-period.  Horizontal dashed line at 0.

All three panels share the same x-axis (period in days, reversed so short periods are on the
right) and carry the same tidal/inertial frequency markers as the existing temperature spectrum:
``M2`` (1.93 h), ``K1`` (23.93 h), ``1.8 d``, and the inertial period at the mooring latitude.

Depth selection
---------------

- Find all pressure levels with at least 5 % of time steps having finite data in **both**
  ``east_velocity`` and ``north_velocity``.
- From those valid levels, pick at most ``N_MAX_LEVELS = 12`` evenly-spaced indices
  (same logic as ``_make_grid_rose_b64``).
- Log the count: ``log(f"Rotary spectrum: {n_sel}/{n_valid} levels plotted")``.
- If fewer than 2 valid levels exist, return ``None`` (section is skipped silently).

Implementation plan
--------------------

**Step 1 — new function ``_rotary_spectra``** (private helper inside the main function)

.. code-block:: python

   def _rotary_spectra(
       u: np.ndarray,   # 1-D, may contain NaN; NaN gap-filled by linear interp before FFT
       v: np.ndarray,
       dt_days: float,
       segment_length: int,
       overlap: float = 0.5,
   ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
       """Return (freq_cpd, S_cw, S_ccw) using Welch cross-spectrum.

       Uses scipy.signal.csd for S_uv and scipy.signal.welch for S_uu, S_vv.
       S_uv is computed with the same Hann window, nperseg, noverlap as S_uu/S_vv.
       Im(S_uv) is the co-spectrum quadrature component; sign convention follows
       Gonella (1972): S_CW uses +2*Im(S_uv).
       """

**Step 2 — new function ``_make_grid_rotary_spectrum_b64``**

Signature (matches existing ``_make_spectrum_fig_b64``):

.. code-block:: python

   def _make_grid_rotary_spectrum_b64(
       ds: "xr.Dataset",
       dt_seconds: float,
       lat: float = 0.0,
       n_max_levels: int = 12,
   ) -> Optional[str]:
       """Three-panel rotary spectrum (CW, CCW, rotary coefficient) for the grid report."""

Location: insert immediately after ``_make_spectrum_fig_b64`` (currently line ~1124 in ``_plots.py``).

**Step 3 — wire up in ``_grid.py``**

In the imports block, add ``_make_grid_rotary_spectrum_b64``.

In context building (after the existing ``fig_spectrum_b64`` call):

.. code-block:: python

   fig_rotary_b64 = _make_grid_rotary_spectrum_b64(ds, _dt_s, lat=_lat)

Pass ``fig_rotary_b64`` to the template render call.

**Step 4 — add to ``_GRID_HTML_TEMPLATE``**

In the nav bar:

.. code-block:: html

   {% if fig_rotary_b64 %}<a href="#rotary">Rotary spectrum</a>{% endif %}

As a section (place it immediately after the power spectrum section, before the Variables table):

.. code-block:: html

   {% if fig_rotary_b64 %}
   <h2 id="rotary">Rotary velocity spectrum</h2>
   <p class="note">CW (clockwise), CCW (counter-clockwise) power spectra and rotary
   coefficient r = (CCW−CW)/(CCW+CW). Welch PSD, Hann window, 14-day segments, 50 %
   overlap. Up to 12 depth levels; colour = pressure (shallow = light blue, deep = dark
   blue). Dashed lines: M2, K1, 1.8 d, inertial period. r &gt; 0 = CCW dominant.</p>
   <details open><summary class="collapse-toggle">show / hide</summary>
   <img class="fig" src="data:image/png;base64,{{ fig_rotary_b64 }}" alt="Rotary spectrum">
   </details>
   {% endif %}

Dependencies
------------

All already available in the environment:

- ``scipy.signal.welch`` and ``scipy.signal.csd`` — used in the existing temperature spectrum
- ``gsw.f(lat)`` — used in the existing temperature spectrum for the inertial frequency
- ``matplotlib`` — standard

Potential issues / open questions
----------------------------------

1. **NaN handling before FFT**: if a level has long gaps (e.g. flagged tidal contamination),
   linear interpolation across the gap will add spurious low-frequency energy.  The
   existing temperature spectrum uses the same workaround.  A better fix would be to
   window around the good data segments — but this is the same trade-off accepted for the
   temperature spectrum, so consistent treatment is appropriate.

2. **Sign convention for** :math:`\mathrm{Im}(S_{uv})`: confirm against Gonella (1972)
   eq. 2.  ``scipy.signal.csd`` returns :math:`S_{uv}^*` with the convention
   ``Cxy = conj(FFT(x)) * FFT(y)``; the imaginary part is the quadrature spectrum with
   sign :math:`+\mathrm{Im}(\hat{u}^* \hat{v})`.  The Gonella rotary decomposition uses
   :math:`S_{CW} = \tfrac{1}{4}(S_{uu} + S_{vv} + 2Q_{uv})` where :math:`Q_{uv}` is the
   quadrature component of the co-spectrum.  Need to verify sign is consistent so that CW
   peaks at the inertial frequency in the Northern Hemisphere.

3. **Segment length**: 14-day Welch segments resolve tidal frequencies well (M2 period
   ~1.93 h) but may be shorter than one inertial period at very low latitudes.  For
   deployments shorter than 28 days the segment length should fall back to ``n_time // 4``.

4. **Y-axis bounds**: CW and CCW spectra share the same y-axis limits so they can be
   visually compared; derive limits from the 1st–99th percentile of all finite PSD values
   across all selected levels.

5. **Whether to show full spectrum or only CW+CCW separate panels**: an alternative is to
   put CW and CCW on the *same* panel (like a two-sided spectrum with period on the x-axis)
   distinguished by line style — solid = CCW, dashed = CW.  This is more compact but harder
   to read.  The three-panel layout is clearer.

References
----------

- Gonella, J. (1972). A rotary-component method for analysing meteorological and
  oceanographic vector time series. *Deep-Sea Research*, 19, 833–846.
- Mooers, C. N. K. (1973). A technique for the cross-spectrum analysis of pairs of
  complex-valued time series with application to drift current and wind waves.
  *Deep-Sea Research*, 20, 1129–1141.
