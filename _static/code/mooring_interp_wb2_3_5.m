
%%  Code to interpolate the mooring data together prior to loading into the
%%  MOC code
%
%
%  Interpolates the temperature and salinity gridded data produced by Julie
%  Collins onto a common time and pressure grid at each of the principal
%  locations.   The anomaly plots at the end are to check for obvious
%  errors in the data processing.
%
%  The output file is saved in the amoc/grdat/ directory for use by the MOC
%  code.
%
%  This code WILL need to be edited for every update of the time series and
%  the time stamp, jg, is a key variable, that will be an input to set the
%  length of the time series.
%
%
% adapted from an original code by Kanzow 2005

% Code History
% -------------------
% v2 - removed the separate pressure grid for WB2 and standardised to the
% normal grid PG
%
% just mochab, mochae, wb2

clear all
close all
clc

warning off

basedir = '/noc/mpoc/';
datadir = [basedir '/rpdmoc/rapid/data/amoc/grout/OCT_2012/annual/'];
% basedir = '/Volumes/';
nowrite = 1;

jlim        = julian([[2004,4,1,0];[2012,11,18,0]]);
jg          = jlim(1):.5:jlim(end);

month       = ['JAN';'FEB';'MAR';'APR';'MAY';'JUN';'JUL';'AUG';'SEP';'OCT';'NOV';'DEC'];
end_date    = gregorian(jg(end));
start_date  = gregorian(jg(1));
end_month   = month(end_date(2),:);
start_month = month(start_date(2),:);
disp('----------------------------');
disp('MOC TIME SERIES');
disp('  ');
disp(['START DATE  = ', num2str(start_date(3)),' ', start_month, ' ', num2str(start_date(1))]);
disp(['END DATE    = ', num2str(end_date(3)),' ', end_month, ' ', num2str(end_date(1))]);
disp('----------------------------');
disp('  ');

PG          = [0 : 20 : 4820]';    % Full depth of moorings
PGmar       = [3700 : 20 : 4820]'; % bit below the MAR crest


%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
%%   3.   LOADING THE GRIDDED MOORING DATA
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

%  Loading the gridded dynamic height mooring data
%  Inputted variables:  jd, p_grid, PG, SGfs, t68(TGfs)


%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
%%  3a WB2
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

lat_wb2 = 26+30.62/60;
lon_wb2 = -76-44.63/60;

% WB2 2004
load([basedir, 'rpdmoc/rapid/data/amoc/'...
    'gridded_profiles/wb2_1_200419_grid.mat']);

SG_wb2_y1  = interp1(p_grid,SGfs,PG);
TG_wb2_y1  = interp1(p_grid,t68(TGfs),PG);
pwb2_y1    = p_grid;
jg_wb2_y1  = jd;

% WB2 2005
load([basedir, 'rpdmoc/rapid/data/amoc/'...
    'gridded_profiles/wb2_2_200528_grid.mat']);

SG_wb2_y2  = interp1(p_grid,SGfs,PG);
TG_wb2_y2  = interp1(p_grid,t68(TGfs),PG);
pwb2_y2    = p_grid;
jg_wb2_y2  = jd;

% WB2 2006 A
load([basedir, 'rpdmoc/rapid/data/amoc/'...
    'gridded_profiles/wb2_3_200606_grid.mat']);

SG_wb2_y3a  = interp1(p_grid,SGfs,PG);
TG_wb2_y3a  = interp1(p_grid,t68(TGfs),PG);
pwb2_y3a    = p_grid;
jg_wb2_y3a  = jd;

% WB2 2006 B
load([basedir, 'rpdmoc/rapid/data/amoc/'...
    'gridded_profiles/wb2_4_200636_grid.mat']);

SG_wb2_y3b  = interp1(p_grid,SGfs,PG);
TG_wb2_y3b  = interp1(p_grid,t68(TGfs),PG);
pwb2_y3b    = p_grid;
jg_wb2_y3b  = jd;

% WB2 2007
load([basedir, 'rpdmoc/rapid/data/amoc/'...
    'gridded_profiles/wb2_5_200702_grid.mat']);

SG_wb2_y4  = interp1(p_grid,SGfs,PG);
TG_wb2_y4  = interp1(p_grid,t68(TGfs),PG);
pwb2_y4    = p_grid;
jg_wb2_y4  = jd;

% WB2 2008
load([basedir, 'rpdmoc/rapid/data/amoc/'...
    'gridded_profiles/wb2_6_200803_grid.mat']);

SG_wb2_y5  = interp1(p_grid,SGfs,PG);
TG_wb2_y5  = interp1(p_grid,t68(TGfs),PG);
pwb2_y5    = p_grid;
jg_wb2_y5  = jd;

% WB2 2009
load([basedir, 'rpdmoc/rapid/data/amoc/'...
    'gridded_profiles/wb2_7_200907_grid.mat']);

SG_wb2_y6  = interp1(p_grid,SGfs,PG);
TG_wb2_y6  = interp1(p_grid,t68(TGfs),PG);
pwb2_y6    = p_grid;
jg_wb2_y6  = jd;

% WB2 2010
load([basedir, 'rpdmoc/rapid/data/amoc/'...
    'gridded_profiles/wb2_8_201003_grid.mat']);

SG_wb2_y7  = interp1(p_grid,SGfs,PG);
TG_wb2_y7  = interp1(p_grid,t68(TGfs),PG);
pwb2_y7    = p_grid;
jg_wb2_y7  = jd;

% WB2 2011
load([basedir, 'rpdmoc/rapid/data/amoc/'...
    'gridded_profiles/wb2_9_201114_grid.mat']);

SG_wb2_y8  = interp1(p_grid,SGfs,PG);
TG_wb2_y8  = interp1(p_grid,t68(TGfs),PG);
pwb2_y8    = p_grid;
jg_wb2_y8  = jd;

% WB2 2010
load([basedir, 'rpdmoc/rapid/data/amoc/'...
    'gridded_profiles/wb2_10_201205_grid.mat']);

SG_wb2_y9  = interp1(p_grid,SGfs,PG);
TG_wb2_y9  = interp1(p_grid,t68(TGfs),PG);
pwb2_y9    = p_grid;
jg_wb2_y9  = jd;

%  interpolating across the gaps and creating a complete matrix of Temp and
%  Salinity profiles TG_wb2 and SG_wb2

TG_wb2 = interp1([jg_wb2_y1 jg_wb2_y2 jg_wb2_y3a jg_wb2_y3b jg_wb2_y4 jg_wb2_y5 jg_wb2_y6 jg_wb2_y7 jg_wb2_y8 jg_wb2_y9]', ...
    [TG_wb2_y1 TG_wb2_y2 TG_wb2_y3a TG_wb2_y3b TG_wb2_y4 TG_wb2_y5 TG_wb2_y6 TG_wb2_y7 TG_wb2_y8 TG_wb2_y9]',jg)';
SG_wb2 = interp1([jg_wb2_y1 jg_wb2_y2 jg_wb2_y3a jg_wb2_y3b jg_wb2_y4 jg_wb2_y5 jg_wb2_y6 jg_wb2_y7 jg_wb2_y8 jg_wb2_y9]', ...
    [SG_wb2_y1 SG_wb2_y2 SG_wb2_y3a SG_wb2_y3b SG_wb2_y4 SG_wb2_y5 SG_wb2_y6 SG_wb2_y7 SG_wb2_y8 SG_wb2_y9]',jg)';


if nowrite
    save([datadir 'wb2_gridded.mat'],...
        'jg', 'PG', 'TG_wb2', 'SG_wb2','lat_wb2','lon_wb2')
end

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
%%  3a WB5 - mochae
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

lat_wb5 = 26.503;
lon_wb5 = -71.978;

% WB5 
load([basedir, 'rpdmoc/rapid/data/amoc/gridded_profiles/'...
    'mochae_1_362_grid.mat']);

SG_wb5_y1  = interp1(p_grid,SGfs,PG);
TG_wb5_y1  = interp1(p_grid,t68(TGfs),PG);
pwb5_y1    = p_grid;
jg_wb5_y1  = jd;

% WB5 
load([basedir, 'rpdmoc/rapid/data/amoc/gridded_profiles/'...
    'mochae_2_368_grid.mat']);

SG_wb5_y2  = interp1(p_grid,SGfs,PG);
TG_wb5_y2  = interp1(p_grid,t68(TGfs),PG);
pwb5_y2    = p_grid;
jg_wb5_y2  = jd;

% WB5 
load([basedir, 'rpdmoc/rapid/data/amoc/gridded_profiles/'...
    'mochae_3_373_grid.mat']);

SG_wb5_y3  = interp1(p_grid,SGfs,PG);
TG_wb5_y3  = interp1(p_grid,t68(TGfs),PG);
pwb5_y3    = p_grid;
jg_wb5_y3  = jd;

% WB5 
load([basedir, 'rpdmoc/rapid/data/amoc/gridded_profiles/'...
    'mochae_4_383_grid.mat']);

SG_wb5_y4  = interp1(p_grid,SGfs,PG);
TG_wb5_y4  = interp1(p_grid,t68(TGfs),PG);
pwb5_y4    = p_grid;
jg_wb5_y4  = jd;

% WB5 
load([basedir, 'rpdmoc/rapid/data/amoc/gridded_profiles/'...
    'mochae_5_392_grid.mat']);

SG_wb5_y5  = interp1(p_grid,SGfs,PG);
TG_wb5_y5  = interp1(p_grid,t68(TGfs),PG);
pwb5_y5    = p_grid;
jg_wb5_y5  = jd;


%  interpolating across the gaps and creating a complete matrix of Temp and
%  Salinity profiles TG_wb5 and SG_wb5

TG_wb5 = interp1([jg_wb5_y1 jg_wb5_y2 jg_wb5_y3 jg_wb5_y4 jg_wb5_y5]', ...
    [TG_wb5_y1 TG_wb5_y2 TG_wb5_y3 TG_wb5_y4 TG_wb5_y5]',jg)';
SG_wb5 = interp1([jg_wb5_y1 jg_wb5_y2 jg_wb5_y3 jg_wb5_y4 jg_wb5_y5]', ...
    [SG_wb5_y1 SG_wb5_y2 SG_wb5_y3 SG_wb5_y4 SG_wb5_y5]',jg)';


if nowrite
    save([datadir 'wb5_gridded.mat'],...
        'jg','PG','TG_wb5','SG_wb5','lat_wb5','lon_wb5');
end

%%

figure(1)
clf
subplot(2,1,1)
contourf(jg - julian(2004,1,1), PG, TG_wb2 - nanmean(TG_wb2,2)*ones(1,length(jg)), 20)
axis ij
shading flat
title('TEMP ANOMALY - WB2'); ylabel('DEPTH')
timeaxis([2004,1,1]);
subplot(2,1,2)
contourf(jg - julian(2004,1,1), PG, SG_wb2 - nanmean(SG_wb2,2)*ones(1,length(jg)), 20)
shading flat
axis ij
title('SALINITY ANOMALY - WB2'); ylabel('DEPTH')
timeaxis([2004,1,1]);

figure(2)
clf
subplot(2,1,1)
contourf(jg - julian(2004,1,1), PG, TG_wb5 - nanmean(TG_wb5,2)*ones(1,length(jg)), 20)
axis ij
shading flat
title('TEMP ANOMALY - wb5'); ylabel('DEPTH')
timeaxis([2004,1,1]);
subplot(2,1,2)
contourf(jg - julian(2004,1,1), PG, SG_wb5 - nanmean(SG_wb5,2)*ones(1,length(jg)), 20)
shading flat
axis ij
title('SALINITY ANOMALY - wb5'); ylabel('DEPTH')
timeaxis([2004,1,1]);


