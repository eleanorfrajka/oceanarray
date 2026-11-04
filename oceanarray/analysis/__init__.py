"""Computations on the processed products that are not pipeline steps.

Modules
-------
hydrography : salinity (calc_psal), isopycnal tracking (isopycnal_pressure_series,
              isopycnal_dataset), cold-regime detection (find_cold_entry_exit) and dataset
              differencing (calc_ds_difference).
spectral    : Gonella rotary spectra (gonella_rotary_spectrum), Welch PSD (welch_psd,
              welch_psd_gapaware) and continuous wavelet transforms (compute_cwt).
"""
