
.. _multisite_merging:

Multi-site Merging
==================

This step combines data from multiple mooring sites near an ocean boundary (east or west) to generate a unified, gridded profile over time and depth, along the continental slope.
It is a crucial part of the RAPID-WB assembly process, ensuring that discontinuous deployments and separate instrument records from neighboring locations (e.g., WB2, WB3, WB4) are stitched into a consistent time–depth matrix.

Rationale
---------

In mooring arrays like RAPID, instruments are deployed and recovered at multiple sites "crawling" up a sloping sidewall.  To estimate transbasin transport, instruments from different zonal locations are merged together to provide a "boundary" profile of dynamic height for the overturning calculation.

For example, in RAPID, the waterdepth at the WB2 mooring site is approximately 3800 dbar.  Data from WB2 are combined with WB3 (a mooring 25km to the east) and, when available, the WBH2 mooring between them.

Input
-----

- Gridded mooring datasets from each site (e.g., `wb1_8_201113_grid.mat`, `wb2_7_200907_grid.mat`).
- Pressure grid: typically `PG = [0, 20, ..., 4820] dbar`.
- Time axis: regular Julian day array, e.g., `jg = 1-Apr-2004 to 11-Oct-2015` in 0.5-day steps.

Steps
-----

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
   Since we now have moorings ending at different depths and from different moorings, they need to be vertically gridded over possible gaps. This re-applies the method from :doc:`vertical_gridding` to concatenate all year-specific fragments into a single time–depth matrix and re-interpolate:
   ::

      TG_wb2 = interp1([jg_y1 jg_y2 ...], [TG_y1 TG_y2 ...], jg);
      SG_wb2 = interp1([jg_y1 jg_y2 ...], [SG_y1 SG_y2 ...], jg);

   Note: I'm not sure this is what RAPID still does. This method has the possibility to create statically unstable profiles of temperature and salinity when merging data from two latitude/longitude positions.

5. **Save merged output**:
   Store combined profiles in NetCDF or `.mat` format for use in dynamic height or MOC calculation:
   - `TG_wb2`, `SG_wb2`
   - Associated `LATITUDE`, `LONGITUDE`, and `JG`

Notes
-----

- Different mooring lines may be merged (e.g., WB1–WB4) to form a composite boundary profile.
- Some scripts distinguish between upstream/downstream or boundary/interior groups before merging.
- Care should be taken when combining profiles with differing depth ranges or quality flags.
