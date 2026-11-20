.. _migrating-0-6-1:

=============================
Upgrading from 0.6.0 to 0.6.1
=============================

The stack keeps flagged values
------------------------------

Before 0.6.1 the stack left out temperature, salinity, pressure and the other
non-velocity values flagged 3, 4 or 9. It now keeps every value, with two flag
sets beside it: ours (``<var>_qc``) and the originator's translated to our table
(``<var>_qc_originator_mapped``). One rule, ``stack: mask_flags`` (default 4),
decides what the stack and Stack cat pages hide and the grid leaves out.

What changes for you:

- **Suspect (3) values are now shown and gridded by default**, for every variable.
  To keep the old behavior, set ``stack: mask_flags: [3, 4]`` in the mooring YAML
  (or ``--mask-flags 3,4``).
- **A level with a value but no flag variable reads 0** (not QC'd), not 9; 9 marks a
  missing value only. Code that treated 9 as "keep" can drop that exception.
- **Read values through the rule** when you use a stack yourself:
  ``oceanarray.pipeline.masking.masked(ds, "salinity")`` returns the values with
  the hidden ones as NaN.
- ``--mask-flags`` on ``run`` now sets the **stack's** rule (what the pages hide and
  what the grid leaves out); before, it set the grid's only. A ``grid: mask_flags`` in
  the YAML adds to it.
- ``grid: mask_flags`` now only **adds** to the stack's rule; one that drops a flag
  the stack hides is refused. The default changed from ``[4, 9]`` to the stack's.
- ``concat`` refuses deployments whose rules differ unless ``--mask-flags`` gives the
  site's.
- Flag tables gain **5 value_changed** (an originator flag saying its QC modified the
  value).

Rebuild existing stacks and grids with ``oceanarray run --force``.

An ADCP needs ``orientation``
-----------------------------

Every ``instrument: ADCP`` entry that is processed now needs ``orientation: up`` or
``orientation: down``. Before, a missing orientation was taken as ``down`` in stage 3,
the stack and the plots, which put an upward-looking ADCP's bins below it. Now
``validate`` reports it as an error and stage 1 refuses the instrument. An RDI header
that disagrees with the YAML is logged and the YAML is used; an imported file that
disagrees is refused.
