"""Computations on the processed products that are not pipeline steps.

Modules
-------
hydrography : salinity (calc_psal), isopycnal tracking (isopycnal_pressure_series,
              isopycnal_dataset), cold-regime detection (find_cold_entry_exit) and dataset
              differencing (calc_ds_difference).
derived     : quantities a report page derives once from its product and passes to
              every figure that needs them: position, potential temperature and
              density, speed and direction, N², knockdown inputs, seabed pressure.
spectral    : Gonella rotary spectra (gonella_rotary_spectrum), Welch PSD (welch_psd,
              welch_psd_gapaware) and continuous wavelet transforms (compute_cwt).
"""
