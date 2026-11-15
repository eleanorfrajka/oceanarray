.. _rapid_heritage:

==============
RAPID heritage
==============

oceanarray grew out of the processing for the RAPID-MOC array at 26°N, done in
MATLAB and stored in the RODB text format. This page keeps that background in one
place: how the instruments are downloaded, the RODB format, the MATLAB scripts the
stages replace, and how RAPID joined deployments and sites. None of it is needed to
run oceanarray.

Downloading MicroCATs at sea
----------------------------

Sea-Bird SBE37 MicroCATs are downloaded with SeaTerm (older firmware) or SeaTermV2.
The older SeaTerm misses a line every 220 lines on the 6000-series instruments, so use
SeaTermV2 for those.

Legacy Firmware (pre-3.0d)
~~~~~~~~~~~~~~~~~~~~~~~~^^

For most instruments with serial numbers below 6000, use **SeaTerm**:

1. Connect the instrument and launch *SeaTerm*.
2. Under *Configure*, select **SBE37**.
3. Choose the correct COM port and set baud rate to **9600**.
4. On the *Upload Settings* tab, check **Upload all data into a single file**.
5. On the *Header Information* tab, ensure **Prompt for Header Information** is checked.
6. Open a capture file (e.g., `5768_recovery.log`).
7. Press Return or click *Connect*—you should see:

   ::

       SBE 37-M
       S>

   (Press return repeatedly if needed.)

8. Type `Stop` to halt logging. Record the time (UTC).
9. Type `DS` and note the clock drift from UTC.
10. Record the “Sample Number” (number of samples taken).
11. Stop the capture file.
12. Click *Upload*.
13. When prompted, enter ship, cruise, and mooring details.
14. Add additional comments (e.g., instrument fouling, damage).
15. Save the file (e.g., `5768_data.asc` or `5768_cal_dip_data.asc`).
16. Confirm upload completion and check contents of `.asc` file.

Modern Firmware (3.0d and above)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^


For instruments with serial numbers >6000 or upgraded units, use **SeaTermV2**:

1. Connect instrument and launch *SeaTermV2* (not regular SeaTerm).
2. Under *Configure*, choose **SBE37** → *SBE 37 RS232*.
3. If auto-connect fails, manually configure baud rate (usually 9600 or 38400).
4. Once connected, open a capture file (e.g., `6338_recovery.log`).
5. Send `DS` to check clock drift relative to UTC.
6. Send `Stop` to halt logging and note the UTC timestamp.
7. (Optional) To increase transfer speed, type `baudrate=38400`.
8. Switch baud rate in software and re-send the command to confirm.
9. Turn off the capture file once response is complete.
10. Click *Upload*, enter filename (e.g., `6338_data.xml`).
11. In the upload dialog:
    - Check **Text Upload**
    - For mooring data, choose **upload as a single file**
    - For cal dips, select **by scan number range**
12. Click *Start* to upload data.
13. After upload, go to *Tools* → *Convert .XML Data File*.
14. Select the XML file and confirm output directory.
15. Under *Output Optional Variables*, check **Date and Time**.
16. Select **Seconds since Jan 1, 2000**.
17. Click OK. This produces a `.CNV` file ready for use in the Stage 1 conversion routines.

**Reference:**
Cunningham, S.A. (2010) RRS Discovery Cruise D344, 21 Oct-18 Nov 2009. RAPID Mooring Cruise Report. Southampton, UK, National Oceanography Centre Southampton, 225pp. (National Oceanography Centre Southampton Cruise Report 51) https://nora.nerc.ac.uk/id/eprint/263915/1/nocscr051.pdf

The RODB format (stage 1)
-------------------------

This standardisation step evolved from the RAPID programme's RODB format, which provided a similar function for historical mooring data. An example of the original RODB format structure:

.. code-block:: text

    Mooring              = wb2_16_2020
    SerialNumber         = 5768
    WaterDepth           = 3916
    InstrDepth           = 1700
    Start_Date           = 2021/01/05
    Start_Time           = 20:00
    End_Date             = 2023/02/25
    End_Time             = 17:00
    Latitude             = 26 31.000 N
    Longitude            = 076 44.460 W
    Columns              = YY:MM:DD:HH:T:C:P
    2021   01   05   20.00056   3.9239   33.2233  1726.4
    2021   01   05   21.00056   3.9389   33.2389  1725.4
    2021   01   05   22.00056   3.9389   33.2405  1725.3

Stage 1 replaces the MATLAB conversion to RODB:

- `microcat2rodb_3.m <_static/code/microcat2rodb_3.m>`__

This script performed similar functions to the current Python implementation:

.. literalinclude:: /_static/code/microcat2rodb_3.m
   :language: matlab
   :lines: 1-40
   :linenos:
   :caption: Excerpt from `microcat2rodb_3.m`

Trimming (stage 2)
------------------

Stage 2 replaces the RAPID trimming script:

- `microcat_raw2use_003.m <_static/code/microcat_raw2use_003.m>`__

.. literalinclude:: /_static/code/microcat_raw2use_003.m
   :language: matlab
   :lines: 1-40
   :linenos:
   :caption: Excerpt from ``microcat_raw2use_003.m``

Joining deployments in time
---------------------------

The RAPID MATLAB route gridded each deployment first and then interpolated the
segments onto one time grid, filling short gaps between deployments. oceanarray
joins the stacks instead and fills nothing.

This step combines multiple mooring deployments at the same site (e.g., WB1, WB2)
into a single continuous time series on a regular time-pressure grid.

Purpose
^^^^^^^
- Interpolate individual deployment periods onto a common pressure grid (e.g., every 20 dbar from 0–4820 dbar).
- Construct a consistent time axis (e.g., half-daily `jg` from April 1, 2004 to October 11, 2015).
- Interpolate all segments onto this unified time grid, filling short gaps between deployments.
- Output gridded `.mat` files for later use in AMOC transport code.

Process overview
^^^^^^^^^^^^^^^^

1. Define a global pressure grid `PG` for all moorings.
2. Load gridded temperature and salinity profiles from each deployment, e.g.:

   - `wb1_2_200527_grid.mat`
   - `wb1_3_200607_grid.mat`
   - ...

3. Interpolate each deployment’s `TGfs` (temperature) and `SGfs` (salinity)
   to the common pressure grid `PG`.
4. Collect and concatenate the corresponding `jd` (Julian day) values for each deployment.
5. Linearly interpolate all values onto the global time grid `jg`.
6. Save the final combined profiles for each mooring site (e.g., `TG_wb1`, `SG_wb1`, etc.)
   along with metadata like location and pressure grid.

Notes
^^^^^
- This process assumes that all individual deployment files have already been vertically interpolated
  (i.e., gridded in pressure).
- The output is suitable for subsequent merging across mooring sites and dynamic height calculation.
- Time interpolation smooths over deployment transitions and data gaps.

Original Source
^^^^^^^^^^^^^^~

Adapted from MATLAB routines by Kanzow (2005) and later modified.

The MATLAB script `mooring_interp_wb2_3_5.m` from the RAPID processing workflow interpolates individual deployments together in time using matlab's `interp1` function. It combines multiple mooring deployments at the same site (e.g., WB2) into a single continuous time series on a regular time-pressure grid.  Note that filenaming within the RAPID project has changed several times without changing the core functionality, but the legacy code provided here may *not* be consistent with the current RAPID processing.


.. _mooring_interp_wb2_3_5.m: _static/code/mooring_interp_wb2_3_5.m


.. literalinclude:: /_static/code/mooring_interp_wb2_3_5.m
   :language: matlab
   :lines: 1-40
   :linenos:
   :caption: Excerpt from `mooring_interp_wb2_3_5.m`

Merging sites into a boundary profile
-------------------------------------

Not implemented in oceanarray.

This step combines data from multiple mooring sites near an ocean boundary (east or west) to generate a unified, gridded profile over time and depth, along the continental slope.
It is a crucial part of the RAPID-WB assembly process, ensuring that discontinuous deployments and separate instrument records from neighboring locations (e.g., WB2, WB3, WB4) are stitched into a consistent time–depth matrix.

Rationale
^^^^^^^^^

In mooring arrays like RAPID, instruments are deployed and recovered at multiple sites "crawling" up a sloping sidewall.  To estimate transbasin transport, instruments from different zonal locations are merged together to provide a "boundary" profile of dynamic height for the overturning calculation.

For example, in RAPID, the waterdepth at the WB2 mooring site is approximately 3800 dbar.  Data from WB2 are combined with WB3 (a mooring 25km to the east) and, when available, the WBH2 mooring between them.

Input
^^^^^

- Gridded mooring datasets from each site (e.g., `wb1_8_201113_grid.mat`, `wb2_7_200907_grid.mat`).
- Pressure grid: typically `PG = [0, 20, ..., 4820] dbar`.
- Time axis: regular Julian day array, e.g., `jg = 1-Apr-2004 to 11-Oct-2015` in 0.5-day steps.

Steps
^^^^^

1. **Load gridded site data**:
   Load the filtered instrument data from each mooring location and extract variables:
   - `jd`: time vector
   - `Tfs`, `Sfs`, `Pfs`: filtered temperature, salinity, and pressure records, and rename them for each deployment as e.g. `Tfs1`, `Tfs2`, etc.


2. **Interpolate to common time grid**:
   Multiple deployments are stacked together (e.g., `Tfs     = [Tfs1;Tfs2;Tfs3a;Tfs3b;Tfs4;Tfs5;Tfs6a;Tfs6b;Tfs7;Tfs8;Tfs9]`) and then sorted vertically for each time.



.. code-block:: matlab

   % all the matrices for the deployments stacked together
   Pfs     = [Pfs1;Pfs2;Pfs3a;Pfs3b;Pfs4;Pfs5;Pfs6a;Pfs6b;Pfs7;Pfs8;Pfs9];
   Sfs     = [Sfs1;Sfs2;Sfs3a;Sfs3b;Sfs4;Sfs5;Sfs6a;Sfs6b;Sfs7;Sfs8;Sfs9];
   Tfs     = [Tfs1;Tfs2;Tfs3a;Tfs3b;Tfs4;Tfs5;Tfs6a;Tfs6b;Tfs7;Tfs8;Tfs9];

   % order the matrices at every time step to avoid too many NaNs creeping in
   % 2004 removed....
   P_sort = NaN .* ones(size(Pfs)); T_sort = NaN .* ones(size(Tfs)); S_sort = NaN .* ones(size(Sfs));
   j = 1;
   for ii = 1: length(JG)
      [P_variable, ix] = sort(Pfs(:, ii));
      P_sort(:,j) = Pfs(ix,ii);
      T_sort(:,j) = Tfs(ix,ii);
      S_sort(:,j) = Sfs(ix,ii);
      j = j + 1;
   end
   % removing unused rows of the sorted matrices
   i = 1; j = 1;
   for i = 1: length(P_sort(:,1))
      ix = find(isnan(P_sort(i,:)));
      if size(ix) < length(JG)
         Pfss(j,:) = P_sort(i, :);
         Tfss(j,:) = T_sort(i, :);
         Sfss(j,:) = S_sort(i, :);
         j = j + 1;
      end
   end

4. **Regrid vertically**:
   Since we now have moorings ending at different depths and from different moorings, they need to be vertically gridded over possible gaps. This re-applies the method from :doc:`methods/vertical_gridding` to concatenate all year-specific fragments into a single time–depth matrix and re-interpolate:
   ::

      TG_wb2 = interp1([jg_y1 jg_y2 ...], [TG_y1 TG_y2 ...], jg);
      SG_wb2 = interp1([jg_y1 jg_y2 ...], [SG_y1 SG_y2 ...], jg);

   Note: I'm not sure this is what RAPID still does. This method has the possibility to create statically unstable profiles of temperature and salinity when merging data from two latitude/longitude positions.

5. **Save merged output**:
   Store combined profiles in NetCDF or `.mat` format for use in dynamic height or MOC calculation:
   - `TG_wb2`, `SG_wb2`
   - Associated `LATITUDE`, `LONGITUDE`, and `JG`

Notes
^^^^^

- Different mooring lines may be merged (e.g., WB1–WB4) to form a composite boundary profile.
- Some scripts distinguish between upstream/downstream or boundary/interior groups before merging.
- Care should be taken when combining profiles with differing depth ranges or quality flags.
