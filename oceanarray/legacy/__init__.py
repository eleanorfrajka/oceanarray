"""Legacy RODB/RAPID format processing functions.

This module contains legacy code for processing RAPID/RODB format data.
For new projects, use the modern CF-compliant workflow:
    oceanarray.process (stages 1-3) -> stack -> grid

Legacy modules:
- process_rodb: Individual instrument processing functions
- mooring_rodb: Mooring-level stacking and filtering functions
- rodb: RODB format data reader
"""

from .convertOS import *
from .mooring_rodb import *

# Import all legacy functionality for backward compatibility
from .process_rodb import *
from .rodb import *

__all__ = [
    # From process_rodb
    "process_instrument",
    "process_microcat",
    "normalize_dataset_by_middle_percent",
    "normalize_by_middle_percent",
    "middle_percent",
    "mean_of_middle_percent",
    "std_of_middle_percent",
    # From mooring_rodb
    "combine_mooring_OS",
    "find_time_vars",
    "find_common_attributes",
    "stack_instruments",
    "interp_to_12hour_grid",
    "get_12hourly_time_grid",
    "filter_all_time_vars",
    "auto_filt",
    # From rodb
    "is_rodb_file",
    "rodbload",
    "rodbsave",
    "format_latlon",
    "parse_rodb_keys_file",
    # From convertOS
    "convert_to_oceansites",
    "load_os_config",
]
