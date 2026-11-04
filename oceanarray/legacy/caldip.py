"""
Calibration dip analysis for oceanographic instrument data.

This module provides functions to compare MicroCAT sensor data with shipboard
CTD data during calibration casts to identify sensor biases and drifts.
The workflow follows procedures from matlab_calibration scripts but implements
them in Python using xarray and modern oceanographic data formats.

Key functionality:
- Water impact detection for time synchronization
- Bottle stop identification and data extraction
- Sensor bias calculation (temperature, conductivity, pressure)
- Conductivity pressure correction for sensors without pressure
- Visualization of calibration results

Based on MATLAB calibration workflow from:
- cp_mc_bot.m, cp_mtd_bot.m 
- insitu_cal.m, microcat_apply_cal_plus.m
- read_botfile.m
"""

import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr
from scipy import signal
from scipy.interpolate import interp1d



def detect_water_impact(
    data: xr.Dataset,
    variable: str = "conductivity",
    threshold: float = 30.0,
    method: str = "threshold",
) -> pd.Timestamp:
    """
    Detect water impact time for instrument synchronization.
    
    Parameters
    ----------
    data : xarray.Dataset
        Instrument data with time coordinate
    variable : str, optional
        Variable to use for impact detection ('conductivity' or 'pressure')
        Default is 'conductivity'
    threshold : float, optional
        Threshold value for impact detection
        Default 30.0 (mS/cm for conductivity, dbar for pressure)
    method : str, optional
        Detection method ('threshold' or 'gradient')
        Default is 'threshold'
    
    Returns
    -------
    pandas.Timestamp
        Time of water impact
        
    Notes
    -----
    For conductivity: detects when C > threshold
    For pressure: detects when P > threshold
    Gradient method uses first derivative to detect rapid change
    """
    if variable not in data.variables:
        raise ValueError(f"Variable '{variable}' not found in dataset")
        
    var_data = data[variable].dropna(dim="time")
    if len(var_data) == 0:
        raise ValueError(f"No valid data found for variable '{variable}'")
    
    if method == "threshold":
        # Find first point above threshold
        impact_mask = var_data > threshold
        impact_indices = np.where(impact_mask)[0]
        
        if len(impact_indices) == 0:
            raise ValueError(f"No data points above threshold {threshold}")
            
        impact_idx = impact_indices[0]
        
    elif method == "gradient":
        # Use gradient to detect rapid change
        gradient = np.gradient(var_data.values)
        # Find maximum gradient (steepest increase)
        impact_idx = np.argmax(gradient)
        
    else:
        raise ValueError("Method must be 'threshold' or 'gradient'")
    
    impact_time = var_data.time.isel(time=impact_idx).values
    return pd.Timestamp(impact_time)


def identify_bottle_stops(
    ctd_data: xr.Dataset,
    bottle_file: Optional[Union[str, Path]] = None,
    bottle_data: Optional[pd.DataFrame] = None,
    pressure_var: str = "pressure",
    min_duration: float = 300.0,
    max_pressure_diff: float = 5.0,
) -> pd.DataFrame:
    """
    Identify bottle stop periods from CTD pressure data or bottle file.
    
    Parameters
    ----------
    ctd_data : xarray.Dataset
        CTD data with time and pressure
    bottle_file : str or Path, optional
        Path to bottle file (SeaBird .btl format or NetCDF)
    bottle_data : pandas.DataFrame, optional
        Pre-loaded bottle stop data
    pressure_var : str, optional
        Name of pressure variable in CTD data, default 'pressure'
    min_duration : float, optional
        Minimum bottle stop duration in seconds, default 300.0 (5 minutes)
    max_pressure_diff : float, optional
        Maximum pressure difference to consider as stable stop, default 5.0 dbar
        
    Returns
    -------
    pandas.DataFrame
        DataFrame with columns: 'start_time', 'end_time', 'pressure', 'duration'
        
    Notes
    -----
    If bottle_file is provided, reads bottle stop times from file.
    Otherwise, automatically detects stops from pressure stability.
    Extended bottle stops (5-10 minutes) are expected for calibration dips.
    """
    if bottle_data is not None:
        # Use provided bottle data
        stops = bottle_data.copy()
    elif bottle_file is not None:
        # Read bottle file
        stops = _read_bottle_file(bottle_file)
    else:
        # Auto-detect from pressure data
        stops = _detect_pressure_stops(
            ctd_data, pressure_var, min_duration, max_pressure_diff
        )
    
    # Filter for minimum duration (calibration dips should be long)
    stops = stops[stops["duration"] >= min_duration].copy()
    
    # Sort by time
    stops = stops.sort_values("start_time").reset_index(drop=True)
    
    return stops


def _read_bottle_file(bottle_file: Union[str, Path]) -> pd.DataFrame:
    """Read bottle stop data from file."""
    bottle_file = Path(bottle_file)
    
    if bottle_file.suffix.lower() == ".nc":
        # NetCDF format
        bottle_ds = xr.open_dataset(bottle_file)
        # Convert to DataFrame - implementation depends on NetCDF structure
        # This is a placeholder - actual implementation would depend on file format
        raise NotImplementedError("NetCDF bottle file reading not yet implemented")
        
    elif bottle_file.suffix.lower() in [".btl", ".ros"]:
        # SeaBird bottle file format
        # This would require parsing SeaBird text format
        # For now, suggest using seabird-scientific package or manual conversion
        raise NotImplementedError(
            "SeaBird bottle file reading not yet implemented. "
            "Consider using seabird-scientific package or convert to NetCDF"
        )
    else:
        raise ValueError(f"Unsupported bottle file format: {bottle_file.suffix}")


def _detect_pressure_stops(
    ctd_data: xr.Dataset,
    pressure_var: str,
    min_duration: float,
    max_pressure_diff: float,
) -> pd.DataFrame:
    """Auto-detect bottle stops from pressure stability."""
    pressure = ctd_data[pressure_var].dropna(dim="time")
    time = pressure.time
    
    # Calculate rolling pressure standard deviation
    window_size = max(10, int(min_duration / 10))  # ~10-second windows
    pressure_std = pressure.rolling(time=window_size, center=True).std()
    
    # Find stable periods (low pressure variability)
    stable_mask = pressure_std < max_pressure_diff
    
    # Find continuous stable periods
    stable_periods = []
    in_stop = False
    start_idx = None
    
    for i, is_stable in enumerate(stable_mask.values):
        if is_stable and not in_stop:
            # Start of stable period
            in_stop = True
            start_idx = i
        elif not is_stable and in_stop:
            # End of stable period
            in_stop = False
            if start_idx is not None:
                duration = (
                    time.isel(time=i).values - time.isel(time=start_idx).values
                ) / np.timedelta64(1, "s")
                
                if duration >= min_duration:
                    stable_periods.append(
                        {
                            "start_time": pd.Timestamp(time.isel(time=start_idx).values),
                            "end_time": pd.Timestamp(time.isel(time=i).values),
                            "pressure": float(
                                pressure.isel(time=slice(start_idx, i)).mean()
                            ),
                            "duration": duration,
                        }
                    )
    
    return pd.DataFrame(stable_periods)


def extract_bottle_stop_data(
    ctd_data: xr.Dataset,
    sensor_data: xr.Dataset,
    bottle_stops: pd.DataFrame,
    comparison_window: float = 180.0,
    exclude_end: float = 30.0,
    ctd_pressure_var: str = "pressure",
    ctd_temp_var: str = "temperature", 
    ctd_cond_var: str = "conductivity",
    sensor_pressure_var: str = "pressure",
    sensor_temp_var: str = "temperature",
    sensor_cond_var: str = "conductivity",
) -> Dict[str, xr.Dataset]:
    """
    Extract data from bottle stops for CTD-sensor comparison.
    
    Parameters
    ----------
    ctd_data : xarray.Dataset
        CTD data with high-frequency sampling (24 Hz)
    sensor_data : xarray.Dataset  
        Sensor data with lower-frequency sampling (0.1 Hz)
    bottle_stops : pandas.DataFrame
        Bottle stop information from identify_bottle_stops()
    comparison_window : float, optional
        Length of comparison window in seconds, default 180.0 (3 minutes)
    exclude_end : float, optional
        Exclude final N seconds from each stop, default 30.0
    ctd_pressure_var : str, optional
        CTD pressure variable name, default 'pressure'
    ctd_temp_var : str, optional  
        CTD temperature variable name, default 'temperature'
    ctd_cond_var : str, optional
        CTD conductivity variable name, default 'conductivity'
    sensor_pressure_var : str, optional
        Sensor pressure variable name, default 'pressure'
    sensor_temp_var : str, optional
        Sensor temperature variable name, default 'temperature'  
    sensor_cond_var : str, optional
        Sensor conductivity variable name, default 'conductivity'
        
    Returns
    -------
    dict
        Dictionary with keys 'ctd' and 'sensor', each containing xarray.Dataset
        with extracted data for each bottle stop
        
    Notes
    -----
    For each bottle stop:
    1. Identify stable pressure period (first 1 minute average)
    2. Extract final 3 minutes excluding last 30 seconds
    3. Filter CTD data to pressure within 5m of bottle stop pressure
    4. Return averaged values and standard deviations
    """
    extracted_data = {"ctd": [], "sensor": []}
    
    for idx, stop in bottle_stops.iterrows():
        stop_start = pd.Timestamp(stop["start_time"])
        stop_end = pd.Timestamp(stop["end_time"])
        
        # Define comparison window (final 3 minutes excluding last 30 seconds)
        comparison_start = stop_end - pd.Timedelta(seconds=comparison_window + exclude_end)
        comparison_end = stop_end - pd.Timedelta(seconds=exclude_end)
        
        # Extract CTD data for this stop
        ctd_stop_mask = (
            (ctd_data.time >= comparison_start) & 
            (ctd_data.time <= comparison_end)
        )
        ctd_stop_data = ctd_data.sel(time=ctd_stop_mask)
        
        # Filter by pressure stability (within 5m of stop pressure)
        if len(ctd_stop_data.time) > 0:
            pressure_mask = (
                np.abs(ctd_stop_data[ctd_pressure_var] - stop["pressure"]) <= 5.0
            )
            ctd_stop_data = ctd_stop_data.where(pressure_mask, drop=True)
        
        # Extract sensor data for this stop  
        sensor_stop_mask = (
            (sensor_data.time >= comparison_start) &
            (sensor_data.time <= comparison_end)
        )
        sensor_stop_data = sensor_data.sel(time=sensor_stop_mask)
        
        # Calculate statistics if sufficient data
        if len(ctd_stop_data.time) > 10 and len(sensor_stop_data.time) > 3:
            # CTD statistics
            ctd_stats = xr.Dataset({
                f"{ctd_pressure_var}_mean": ctd_stop_data[ctd_pressure_var].mean(),
                f"{ctd_pressure_var}_std": ctd_stop_data[ctd_pressure_var].std(),
                f"{ctd_temp_var}_mean": ctd_stop_data[ctd_temp_var].mean(),
                f"{ctd_temp_var}_std": ctd_stop_data[ctd_temp_var].std(),
                f"{ctd_cond_var}_mean": ctd_stop_data[ctd_cond_var].mean(),
                f"{ctd_cond_var}_std": ctd_stop_data[ctd_cond_var].std(),
                "stop_number": idx,
                "stop_pressure": stop["pressure"],
            })
            
            # Sensor statistics
            sensor_stats = xr.Dataset({
                f"{sensor_temp_var}_mean": sensor_stop_data[sensor_temp_var].mean(),
                f"{sensor_temp_var}_std": sensor_stop_data[sensor_temp_var].std(),
                f"{sensor_cond_var}_mean": sensor_stop_data[sensor_cond_var].mean(), 
                f"{sensor_cond_var}_std": sensor_stop_data[sensor_cond_var].std(),
                "stop_number": idx,
                "stop_pressure": stop["pressure"],
            })
            
            # Add pressure stats if available
            if sensor_pressure_var in sensor_stop_data.variables:
                sensor_stats[f"{sensor_pressure_var}_mean"] = sensor_stop_data[sensor_pressure_var].mean()
                sensor_stats[f"{sensor_pressure_var}_std"] = sensor_stop_data[sensor_pressure_var].std()
            
            extracted_data["ctd"].append(ctd_stats)
            extracted_data["sensor"].append(sensor_stats)
    
    # Combine into datasets
    if extracted_data["ctd"]:
        extracted_data["ctd"] = xr.concat(extracted_data["ctd"], dim="stop")
        extracted_data["sensor"] = xr.concat(extracted_data["sensor"], dim="stop")
    else:
        # Return empty datasets if no valid stops found
        extracted_data["ctd"] = xr.Dataset()
        extracted_data["sensor"] = xr.Dataset()
    
    return extracted_data


def calculate_sensor_offsets(
    comparison_data: Dict[str, xr.Dataset],
    apply_pressure_correction: bool = True,
    reference_pressure: Optional[float] = None,
) -> xr.Dataset:
    """
    Calculate sensor offsets relative to CTD reference.
    
    Parameters
    ----------
    comparison_data : dict
        Output from extract_bottle_stop_data()
    apply_pressure_correction : bool, optional
        Apply pressure correction to conductivity for sensors without pressure
        Default True
    reference_pressure : float, optional
        Reference pressure for conductivity correction
        If None, uses CTD pressure for each stop
        
    Returns
    -------
    xarray.Dataset
        Dataset containing offset calculations:
        - dt: temperature difference (sensor - CTD) [°C]
        - dc: conductivity difference (sensor - CTD) [mS/cm]  
        - dp: pressure difference (sensor - CTD) [dbar]
        - ctd_std_*: CTD standard deviations
        - sensor_std_*: sensor standard deviations
        
    Notes
    -----
    For sensors without pressure, conductivity is corrected using CTD pressure
    before calculating differences. This accounts for pressure effects on
    conductivity cell geometry.
    """
    ctd_data = comparison_data["ctd"]
    sensor_data = comparison_data["sensor"]
    
    if len(ctd_data.stop) == 0:
        warnings.warn("No valid bottle stop data found")
        return xr.Dataset()
    
    # Calculate temperature offset
    dt = sensor_data["temperature_mean"] - ctd_data["temperature_mean"]
    
    # Calculate conductivity offset
    sensor_cond = sensor_data["conductivity_mean"].copy()
    
    # Apply pressure correction to conductivity if needed
    has_sensor_pressure = "pressure_mean" in sensor_data.variables
    
    if apply_pressure_correction and not has_sensor_pressure:
        # Correct sensor conductivity for pressure effects
        if reference_pressure is None:
            ref_pressure = ctd_data["pressure_mean"]
        else:
            ref_pressure = xr.full_like(ctd_data["pressure_mean"], reference_pressure)
            
        # Apply pressure correction (simplified linear model)
        # More sophisticated correction would use actual sensor calibration coefficients
        pressure_correction_factor = 2.2e-6  # Typical value for SeaBird sensors
        pressure_diff = ctd_data["pressure_mean"] - ref_pressure
        sensor_cond = sensor_cond * (1 + pressure_correction_factor * pressure_diff)
    
    dc = sensor_cond - ctd_data["conductivity_mean"]
    
    # Create output dataset
    offsets = xr.Dataset({
        "dt": dt,
        "dc": dc, 
        "pressure_stop": ctd_data["stop_pressure"],
        "ctd_temperature_std": ctd_data["temperature_std"],
        "ctd_conductivity_std": ctd_data["conductivity_std"],
        "ctd_pressure_std": ctd_data["pressure_std"],
        "sensor_temperature_std": sensor_data["temperature_std"],
        "sensor_conductivity_std": sensor_data["conductivity_std"],
    })
    
    # Add pressure offset if available
    if has_sensor_pressure:
        dp = sensor_data["pressure_mean"] - ctd_data["pressure_mean"]
        offsets["dp"] = dp
        offsets["sensor_pressure_std"] = sensor_data["pressure_std"]
    
    # Add metadata
    offsets.attrs.update({
        "title": "Sensor calibration offsets relative to CTD",
        "description": "Temperature, conductivity, and pressure differences calculated from bottle stop comparisons",
        "pressure_correction_applied": apply_pressure_correction and not has_sensor_pressure,
    })
    
    return offsets


def plot_calibration_results(
    ctd_data: xr.Dataset,
    sensor_data: xr.Dataset, 
    bottle_stops: pd.DataFrame,
    offsets: xr.Dataset,
    sensor_serial: Optional[str] = None,
    figsize: Tuple[float, float] = (12, 10),
) -> plt.Figure:
    """
    Plot calibration dip results showing CTD vs sensor data.
    
    Parameters
    ----------
    ctd_data : xarray.Dataset
        Full CTD dataset
    sensor_data : xarray.Dataset
        Full sensor dataset  
    bottle_stops : pandas.DataFrame
        Bottle stop information
    offsets : xarray.Dataset
        Calculated offsets from calculate_sensor_offsets()
    sensor_serial : str, optional
        Sensor serial number for plot title
    figsize : tuple, optional
        Figure size (width, height), default (12, 10)
        
    Returns
    -------
    matplotlib.Figure
        Figure with three subplots showing pressure, temperature, and conductivity
        
    Notes
    -----
    Creates three-panel plot:
    - Top: CTD pressure (thick black) and sensor pressure (thin colored)
    - Middle: CTD and sensor temperature with bottle stop markers
    - Bottom: CTD and sensor conductivity with bottle stop markers
    
    Bottle stops are marked with vertical lines and pressure annotations.
    """
    fig, axes = plt.subplots(3, 1, figsize=figsize, sharex=True)
    
    # Convert times to matplotlib format for plotting
    ctd_time = pd.to_datetime(ctd_data.time.values)
    sensor_time = pd.to_datetime(sensor_data.time.values)
    
    # Top panel: Pressure
    axes[0].plot(ctd_time, ctd_data.pressure, "k-", linewidth=2, label="CTD 911")
    
    if "pressure" in sensor_data.variables:
        axes[0].plot(
            sensor_time, 
            sensor_data.pressure, 
            "r-", 
            linewidth=1, 
            label=f"Sensor {sensor_serial or 'XXXX'}"
        )
    
    axes[0].set_ylabel("Pressure [dbar]")
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)
    axes[0].invert_yaxis()  # Pressure increases downward
    
    # Middle panel: Temperature  
    axes[1].plot(ctd_time, ctd_data.temperature, "k-", linewidth=2, label="CTD 911")
    axes[1].plot(
        sensor_time, 
        sensor_data.temperature, 
        "r-", 
        linewidth=1, 
        label=f"Sensor {sensor_serial or 'XXXX'}"
    )
    axes[1].set_ylabel("Temperature [°C]")
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)
    
    # Bottom panel: Conductivity
    axes[2].plot(ctd_time, ctd_data.conductivity, "k-", linewidth=2, label="CTD 911")
    axes[2].plot(
        sensor_time, 
        sensor_data.conductivity, 
        "r-", 
        linewidth=1, 
        label=f"Sensor {sensor_serial or 'XXXX'}"
    )
    axes[2].set_ylabel("Conductivity [mS/cm]")
    axes[2].set_xlabel("Time")
    axes[2].legend()
    axes[2].grid(True, alpha=0.3)
    
    # Mark bottle stops
    for _, stop in bottle_stops.iterrows():
        stop_time = pd.Timestamp(stop["start_time"])
        for ax in axes:
            ax.axvline(stop_time, color="blue", alpha=0.5, linestyle="--")
            
        # Add pressure annotation on top panel
        axes[0].annotate(
            f"{stop['pressure']:.0f}m",
            xy=(stop_time, stop["pressure"]),
            xytext=(10, 10),
            textcoords="offset points",
            fontsize=8,
            color="blue",
        )
    
    # Add offset information as text
    if len(offsets.stop) > 0:
        offset_text = []
        if "dt" in offsets:
            dt_mean = float(offsets.dt.mean())
            dt_std = float(offsets.dt.std()) 
            offset_text.append(f"ΔT = {dt_mean:.4f} ± {dt_std:.4f} °C")
            
        if "dc" in offsets:
            dc_mean = float(offsets.dc.mean())
            dc_std = float(offsets.dc.std())
            offset_text.append(f"ΔC = {dc_mean:.4f} ± {dc_std:.4f} mS/cm")
            
        if "dp" in offsets:
            dp_mean = float(offsets.dp.mean())
            dp_std = float(offsets.dp.std())
            offset_text.append(f"ΔP = {dp_mean:.2f} ± {dp_std:.2f} dbar")
        
        # Add text box with offsets
        text_str = "\n".join(offset_text)
        axes[1].text(
            0.02, 0.98, text_str, 
            transform=axes[1].transAxes,
            verticalalignment="top",
            bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.8),
            fontsize=9,
        )
    
    # Format and finalize
    plt.suptitle(
        f"Calibration Dip Results - Sensor {sensor_serial or 'XXXX'}", 
        fontsize=14
    )
    plt.tight_layout()
    
    return fig


def process_calibration_dip(
    ctd_file: Union[str, Path],
    sensor_file: Union[str, Path], 
    bottle_file: Optional[Union[str, Path]] = None,
    output_file: Optional[Union[str, Path]] = None,
    plot_results: bool = True,
    **kwargs
) -> Dict[str, Union[xr.Dataset, pd.DataFrame, plt.Figure]]:
    """
    Complete calibration dip processing workflow.
    
    Parameters
    ----------
    ctd_file : str or Path
        Path to CTD NetCDF file (24 Hz data)
    sensor_file : str or Path  
        Path to sensor NetCDF file (10 second data)
    bottle_file : str or Path, optional
        Path to bottle stop file
    output_file : str or Path, optional
        Path to save results NetCDF file
    plot_results : bool, optional
        Generate calibration plots, default True
    **kwargs
        Additional arguments passed to processing functions
        
    Returns
    -------
    dict
        Dictionary containing:
        - 'offsets': xarray.Dataset with calculated offsets
        - 'bottle_stops': pandas.DataFrame with bottle stop information  
        - 'ctd_data': xarray.Dataset with CTD data
        - 'sensor_data': xarray.Dataset with sensor data
        - 'figure': matplotlib.Figure (if plot_results=True)
        
    Examples
    --------
    >>> # Basic usage
    >>> results = process_calibration_dip('ctd_data.nc', 'sensor_data.nc')
    >>> offsets = results['offsets']
    >>> print(f"Mean temperature offset: {offsets.dt.mean().values:.4f} °C")
    
    >>> # With bottle file and custom parameters
    >>> results = process_calibration_dip(
    ...     'ctd_data.nc', 'sensor_data.nc', 
    ...     bottle_file='bottle_stops.nc',
    ...     comparison_window=240.0,  # 4 minute window
    ...     plot_results=True
    ... )
    """
    # Load data
    print(f"Loading CTD data from {ctd_file}")
    ctd_data = xr.open_dataset(ctd_file)
    
    print(f"Loading sensor data from {sensor_file}")  
    sensor_data = xr.open_dataset(sensor_file)
    
    # Get sensor serial if available
    sensor_serial = getattr(sensor_data, "serial_number", None)
    if sensor_serial is None and "serial_number" in sensor_data.variables:
        sensor_serial = str(sensor_data.serial_number.values)
    
    # Detect water impacts for time synchronization
    try:
        ctd_impact = detect_water_impact(ctd_data, **kwargs.get("ctd_impact_kwargs", {}))
        sensor_impact = detect_water_impact(sensor_data, **kwargs.get("sensor_impact_kwargs", {}))
        
        time_offset = (ctd_impact - sensor_impact).total_seconds()
        print(f"Time offset CTD-Sensor: {time_offset:.1f} seconds")
        
        # Apply time offset to sensor data if significant  
        if abs(time_offset) > 5:  # More than 5 seconds
            print("Applying time offset to sensor data")
            sensor_data["time"] = sensor_data.time + pd.Timedelta(seconds=time_offset)
            
    except (ValueError, KeyError) as e:
        print(f"Warning: Could not determine time offset: {e}")
        print("Proceeding without time synchronization")
    
    # Identify bottle stops
    print("Identifying bottle stops")
    bottle_stops = identify_bottle_stops(
        ctd_data, 
        bottle_file=bottle_file,
        **kwargs.get("bottle_stop_kwargs", {})
    )
    
    print(f"Found {len(bottle_stops)} bottle stops")
    if len(bottle_stops) == 0:
        raise ValueError("No bottle stops found - check CTD data or bottle file")
    
    # Extract comparison data
    print("Extracting bottle stop data")
    comparison_data = extract_bottle_stop_data(
        ctd_data, 
        sensor_data, 
        bottle_stops,
        **kwargs.get("extraction_kwargs", {})
    )
    
    if len(comparison_data["ctd"].stop) == 0:
        raise ValueError("No valid bottle stop data extracted")
    
    # Calculate offsets
    print("Calculating sensor offsets")
    offsets = calculate_sensor_offsets(
        comparison_data,
        **kwargs.get("offset_kwargs", {})
    )
    
    # Create results dictionary
    results = {
        "offsets": offsets,
        "bottle_stops": bottle_stops, 
        "ctd_data": ctd_data,
        "sensor_data": sensor_data,
    }
    
    # Generate plots
    if plot_results:
        print("Generating calibration plots")
        fig = plot_calibration_results(
            ctd_data, sensor_data, bottle_stops, offsets, 
            sensor_serial=sensor_serial,
            **kwargs.get("plot_kwargs", {})
        )
        results["figure"] = fig
    
    # Save results
    if output_file is not None:
        print(f"Saving results to {output_file}")
        offsets.to_netcdf(output_file)
    
    # Print summary
    if len(offsets.stop) > 0:
        print("\nCalibration Results Summary:")
        print(f"Number of bottle stops: {len(offsets.stop)}")
        
        if "dt" in offsets:
            dt_mean = float(offsets.dt.mean())
            dt_std = float(offsets.dt.std())
            print(f"Temperature offset: {dt_mean:.4f} ± {dt_std:.4f} °C")
            
        if "dc" in offsets:  
            dc_mean = float(offsets.dc.mean())
            dc_std = float(offsets.dc.std())
            print(f"Conductivity offset: {dc_mean:.4f} ± {dc_std:.4f} mS/cm")
            
        if "dp" in offsets:
            dp_mean = float(offsets.dp.mean())
            dp_std = float(offsets.dp.std())
            print(f"Pressure offset: {dp_mean:.2f} ± {dp_std:.2f} dbar")
    
    return results