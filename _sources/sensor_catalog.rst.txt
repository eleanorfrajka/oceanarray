.. _sensor_catalog:

==================
The sensor catalog
==================

A sensor catalog says what each instrument is, by its serial number: the model,
the maker and their entries in the NERC vocabularies. One catalog can serve every
mooring of a lab or a project, so an instrument used on several moorings is
described once. It holds no calibrations; those stay with the calibration dips
(:doc:`calibration_dips`).

Stage 1 uses it to write the instrument's sensor record (the ``SENSOR_<type>_<serial>``
variable, following OG1, since OceanSITES does not describe sensors). Without a
catalog, the record carries only what the instrument file itself states.

Today the catalog covers SBE 37 MicroCATs.

An example
----------

.. code-block:: yaml

   models:
     SBE 37-SMP:
       long_name: Sea-Bird SBE 37-SMP MicroCAT
       sensor_type: CTD
       sensor_type_vocabulary: https://vocab.nerc.ac.uk/collection/L05/current/130/
       sensor_maker: Sea-Bird Scientific
       sensor_maker_vocabulary: https://vocab.nerc.ac.uk/collection/L35/current/MAN0013/
       with_pressure:
         sensor_model: Sea-Bird SBE 37 MicroCat SMP-CTP (submersible) CTD sensor
         sensor_model_vocabulary: https://vocab.nerc.ac.uk/collection/L22/current/TOOL1457/
       without_pressure:
         sensor_model: Sea-Bird SBE 37 MicroCat SMP-CT with optional pressure (submersible) CTD sensor series
         sensor_model_vocabulary: https://vocab.nerc.ac.uk/collection/L22/current/TOOL0018/

   instruments:
     "37-12046":
       model: SBE 37-SMP
       pumped: true
       sources:
         - {report: 2015_64PE400_NIOZ_cruise_report.pdf, pdf_page: 40,
            text: "Instrument - MicroCat SBE 37-SMP SN 12046 88m --> 172 m"}

``models``
   One entry per model: ``long_name``, ``sensor_type`` and ``sensor_maker`` with
   their vocabularies (NERC L05 and L35). The SeaVoX device catalogue (L22) lists a
   model with and without a pressure sensor separately, so each model gives both,
   as ``with_pressure`` and ``without_pressure``. A MicroCAT with an optode is a
   model of its own (``SBE 37-SMP-ODO``).

``instruments``
   One entry per instrument, keyed by the maker's label (``"37-12046"`` is SBE 37
   MicroCAT 12046): its ``model``, whether it is ``pumped``, and ``sources``, the
   documents that say so (a report, its page, the words quoted).

What stage 1 does with it
-------------------------

For each MicroCAT, stage 1 looks the instrument up by the serial in its mooring
YAML entry, then by the serial its file states. When found, the sensor record gets
the model's attributes and the ``with_pressure`` or ``without_pressure`` entry,
depending on whether the instrument measured pressure (values present, and at least
one flagged good or none quality-controlled). ``sensor_pumped`` records the pumping,
and ``sensor_model_source`` the documents.

An instrument entry's own ``sensor_model`` in the mooring YAML wins over the
catalog. When the instrument file states a different model, the file's value is
kept as ``originator_sensor_model`` and a warning is logged.

Stage 1 does not rerun when only the catalog changes; rerun with
``oceanarray run --force``.

Using a catalog
---------------

Name the catalog in the mooring YAML (``sensor_catalog:``, relative to the mooring
YAML). *Planned:* the catalog's location moves to the call and the command line,
like the other input locations, so the mooring YAML holds no path.
