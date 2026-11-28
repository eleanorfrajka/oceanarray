% 1) dedrift microcats using post- and pre-cruise  ctd calibration 
% 2) set suspicious data to dummy 
%
% prior to this programme, microcat_insitu_cal.m has to be run for pre- and post-cruise calibration
% calibration offset then have to be entered in 'microcat_cond.xls' /
% 'microcat_temp.xls' / microcat_presX.xls (with X being the number of
% deployment
%
%

% kanzow, 23.08.05 

clear
warning off
operator  = 'JC';
%%mooring   = 'mochae_1_362';    % M1; M2; M3
mooring   = 'eb1_2_200516';    % M1; M2; M3
sensortyp = 'microcat';   % arg / microcat

delim = ',';

% input directories & files 
rodbpath('/noc/ooc/rpdmoc/users/tok/rapid/data/'); 
mooring_dir         = '/noc/ooc/rpdmoc/rapid/data/moor/proc/';
mooring_outdir         = '/noc/ooc/rpdmoc/users/jcol/';
coef_dir            = '/noc/ooc/rpdmoc/users/jcol/cal_coef/';

external_ctd        = '/noc/ooc/rpdmoc/users/tok/rapid/data/cruise/d279/ctd/';
external_ctd_file   = 'd279_pos.mat';

% extract calibration cruise indices from database (.xls spreadsheet)

%[cruiseI,moors,raw]  = xlsread([coef_dir,'microcat_calib_cruise.xls']);
fid_cruise = fopen([coef_dir,'microcat_calib_cruise.csv']);
cruise     = textscan(fid_cruise,'%s%d%d','delimiter',delim,'HeaderLines',2);
fclose(fid_cruise)

moorI                = find((strcmp(cruise{1},mooring))>0);
cruise               = [cruise{2} cruise{3}]; 
cruiseI              = cruise(moorI,:);

% ---  output  data ---- 

%%ext      = '.microcat';
dum      = -9999; 
pref     = 0;
t90_68   = 1.00024;  % convert its90 to its68 for cond. to sal.

%%conversion
cols      = 'YY:MM:DD:HH:T:C'; % column info for rodb header
colsp     = 'YY:MM:DD:HH:T:C:P'; % column info for rodb header (mc with pressure sensor)
colsarg   = 'YY:MM:DD:HH:T:TCAT:P:PCAT:C:U:V:W';
fort      = '%4.4d  %2.2d  %2.2d  %7.5f   %6.4f  %6.4f'; %data output format
fortp     = '%4.4d  %2.2d  %2.2d  %7.5f   %6.4f  %6.4f  %5.1f'; %data output format(mc with pressure sensor)  

% sensor specific settings

if strcmp('microcat',sensortyp)
  typ_id = [333 337];
elseif strcmp('arg',sensortyp)
  typ_id = [366];  
end

ext = ['.',sensortyp];
% ---- microcat raw data ----

head    = ['Mooring:SerialNumber:WaterDepth:InstrDepth:Start_Date:Start_Time:End_Date:End_Time:Latitude:Longitude:Columns'];


% ---- load calib offsets (pre and post cruise) ----

% Conductivity / Temperature / Depth
if strcmp('microcat',sensortyp)
 
  fidt  = fopen([coef_dir,'microcat_temp.csv'],'r');
  ttext = textscan(fidt,'%s%s%s%s%s%s%s','delimiter',delim);
  fseek(fidt,0,'bof');
  tnum  = textscan(fidt,'%f%f%s%s%f%s%s','delimiter',delim,'HeaderLines',5);
  
  
  fidc  = fopen([coef_dir,'microcat_cond.csv'],'r');
  ctext = textscan(fidc,'%s%s%s%s%s%s%s','delimiter',delim);
  fseek(fidc,0,'bof');
  cnum  = textscan(fidc,'%f%f%s%s%f%s%s','delimiter',delim,'HeaderLines',5);


  fidp  = fopen([coef_dir,'microcat_pres.csv'],'r');
  ptext = textscan(fidp,'%s%s%s%s%s%s%s%s%s%s%s%s%s','delimiter',delim);
  fseek(fidp,0,'bof');
  pnum  = textscan(fidp,'%f%s%f%s%s%s%f%s%s%s%f%s%s','delimiter',delim,'HeaderLines',5);

elseif strcmp('arg',sensortyp)
  [cnum,ctext] = xlsread([coef_dir,'microcat_cond.xls']);
  [tnum,ttext] = xlsread([coef_dir,'microcat_temp.xls']);
  [pnum,ptext] = xlsread([coef_dir,'microcat_pres.xls']);
  [targnum,targtext] = xlsread([coef_dir,'argonaut_temp.xls']);
  [pargnum,pargtext] = xlsread([coef_dir,'argonaut_pres.xls']);
  targin             =   targnum(:,1);  % Instruments
  pargin             =   pargnum(:,1);  % Instruments

end

% ---- pick instruments ---------------

cin    =   cnum{1};   % Instruments
tin    =   tnum{1};   % Instruments
pin   = pnum{1};
plen  = length(ptext);
clen  = length(ctext);
tlen  = length(ttext);

% ------ pre cruise calibration ----------------

if cruiseI(1) < 0
    preC(1:length(cin),1) = NaN;
    preT(1:length(tin),1) = NaN;
    preP(1:length(pin),1) = NaN;
    preCcomment(1:length(cin),1) = {' '};
    preTcomment(1:length(tin),1) = {' '};
    prePcomment(1:length(pin),1) = {' '};
    
else

  %%  ccol  = find(cnum(2,:) == cruiseI(1));
  for i = 1 :clen
    cvalI(i)=strcmp(ctext{i}{2},num2str(cruiseI(1)));
  end
  ccol = find(cvalI == 1);

  for i = 1 :tlen
    tvalI(i)=strcmp(ttext{i}{2},num2str(cruiseI(1)));
  end
  tcol = find(tvalI == 1);

  
   for i = 1 :plen
    pvalI(i)=strcmp(ptext{i}{2},num2str(cruiseI(1)));
  end
  pcol = find(pvalI == 1);
 
  preC  =  cnum{ccol}; %pre cruise offsets
  preCcomment =  cnum{ccol+2};
  
  preT  =  tnum{tcol}; %pre cruise offsets
  preTcomment =  tnum{tcol+2};
  
    if ~isempty(find(strcmp(ptext{pcol(1)-1},mooring)>0))
  
    preP  = pnum{pcol(1)};
    prePcomment =  pnum{pcol(1)+2};
  
  elseif ~isempty(find(strcmp(ptext{pcol(2)-1},mooring)>0))
      %%preP  = pnum(:,pcol(2)); 
      preP  = pnum{pcol(2)}; 
      prePcomment =  pnum{pcol(2)+2};
  end
end

% ----- post cruise calibration -----------------

if cruiseI(2) < 0

  postC(1:length(cin),1) = NaN;
  postT(1:length(tin),1) = NaN;
  postP(1:length(pin),1) = NaN;
    postCcomment(1:length(cin),1) = {' '};
    postTcomment(1:length(tin),1) = {' '};
    postPcomment(1:length(pin),1) = {' '};

else

  for i = 1 :clen
    cvalI(i)=strcmp(ctext{i}{2},num2str(cruiseI(2)));
  end
  ccol = find(cvalI == 1);

  for i = 1 :tlen
    tvalI(i)=strcmp(ttext{i}{2},num2str(cruiseI(2)));
  end
  tcol = find(tvalI == 1);
  
    for i = 1 :plen
     pvalI(i)=strcmp(ptext{i}{2},num2str(cruiseI(2)));
  end
  pcol = find(pvalI == 1);

  postC  = cnum{ccol}; %post cruise offsets
  postCcomment =  cnum{ccol+2};  
  
  postT  = tnum{tcol}; %post cruise offsets
  postTcomment =  tnum{tcol+2};  
  
  
    
  if ~isempty(find(strcmp(ptext{pcol(1)-1},mooring)>0))
  
    postP  = pnum{pcol(1)};
    postPcomment =  pnum{pcol(1)+2};
  
  elseif ~isempty(find(strcmp(ptext{pcol(2)-1},mooring)>0))
      postP  = pnum{pcol(2)}; 
      postPcomment =  pnum{pcol(2)+2};
  end
end

% General trend: to be applied when pre - or post cruise calibration is 
% missing for individual instruments

nnan   = find(~isnan(postC) & ~isnan(preC));%lines 1:5 are header

val    = find(abs(postC(nnan))<0.02 & abs(preC(nnan)) < 0.02);

Ctrend = -mean(preC(nnan(val)))+mean(postC(nnan(val)));  %allg. Vergleich pre - post cruise 
Ttrend = -mean(preT(nnan(val)))+mean(postT(nnan(val)));

if isempty(val) 
  Ctrend = 0; Ttrend = 0;   
end

nnan   = find(~isnan(postP) & ~isnan(preP));%lines 1:5 are header
val    = find(abs(postP(nnan))<100 & abs(preP(nnan)) < 100);
Ptrend = -mean(preP(nnan(val)))+mean(postP(nnan(val)));

if isempty(val) 
  Ptrend = 0;    
end


%%% ---- 1) apply dedrift --------- %%%%%%%%

[typ,dep,serial] = rodbload([mooring_dir,mooring,'/',mooring,'info.dat'],...
                        ['instrument:z:serialnumber']);

mcI     = find(typ >= typ_id(1) & typ <= typ_id(end));
dep     = dep(mcI);
serial  = serial(mcI);
typ     = typ(mcI);

for mc = 1 : length(serial),

  %%%%mcfile_out = [mooring_dir,mooring,'/',sensortyp,'/',mooring,'_',sprintf('%3.3d',mc),ext]; 
  mcfile_out = [mooring_outdir,mooring,'/',sensortyp,'/',mooring,'_',sprintf('%3.3d',mc),ext]; 
  if exist(mcfile_out) == 2 
    over = input([mcfile_out,' exists already: do you want to overwrite it? y/n '],'s');  
    
    if ~strcmp(over,'y')
         continue
    end
    [succ1,mess1] =copyfile(mcfile_out,[mcfile_out,date]);
    [succ2,mess2] =copyfile([mcfile_out,'.txt'],[mcfile_out,'.txt',date]);

  end  
    
  skipC  = 1;
  skipT  = 1;
  skipP  = 1;
  
  mcfile = [mooring_dir,mooring,'/',sensortyp,'/',mooring,'_',sprintf('%4.4d',serial(mc)),'.use'];

  if strcmp(sensortyp,'microcat')

    [yy,mm,dd,hh,t,c,p]= rodbload(mcfile,colsp);
  elseif strcmp(sensortyp,'arg')
    [yy,mm,dd,hh,t,tcat,p,pcat,c,u,v,w]= rodbload(mcfile,colsarg);
  end      
      
  [moo,serial0,wd,id,sd,st,ed,et,lt,ln,cl] = rodbload(mcfile,head);

  jd       = julian(yy,mm,dd,hh);
  jd0      = jd - jd(1);  
  xli      = [0 max(jd0)];

  spikeT   = find(t<-900);
  spikeC   = find(c<-900); 
  spikeP   = find(p<-0); 

  cinI     = find(cin == serial0);
  tinI     = find(tin == serial0); 
  p_exist  = 'n';
  sp       = 3;
  if ~isempty(find(~isnan(p)))
    p_exist = 'y';
    pinI     = find(pin == serial0); 
    sp       = 4;   
  end

  warn = 0;
  if isempty(cinI)
     disp(['no conduct. calibration entry found for serial number ',num2str(serial0)])
     skipC    =  input('skip (0) or save data without calibration(1) ');
     corrC    =  0;
  else
    cal1     =  preC(cinI);
    cal1comment = preCcomment(cinI);
    cal2     =  postC(cinI);       
    cal2comment = postCcomment(cinI);
    
    
    disp('   ')
    disp(['Conductivity pre-cruise calibration: ',num2str(cal1)])
    disp(['Comment: ',cal1comment{1}])
    disp(['Conductivity post-cruise calibration: ',num2str(cal2)])
    disp(['Comment: ',cal2comment{1}])
    disp('  ')  
    keep = input('Type 0, 1 or 2 if you want to use both, the 1st or the 2nd coefficient ');
    disp('  ')
    if keep == 1
      postC(cinI) = NaN;
      disp('Only using the pre cruise calibration coefficient')
    elseif keep == 2
      preC(cinI) = NaN;
      disp('Only using the post cruise calibration coefficient')
    elseif keep == 0
      disp('Using both calibration coefficients')
    else
      disp(['Choice ',num2str(keep),' not defined'])
      break
    end
        
    cal1     =  preC(cinI);
    cal2     =  postC(cinI);       

    if isnan(cal1)
      applytrend = input('SHOULD GENERAL TREND FOR CONDUCTIVITIES BE APPLIED? [y/n]','s');
      if strcmp(applytrend,'y')
        cal1   =  cal2-Ctrend;
      else
        cal1   =  cal2-Ctrend*0;
      end    
      warn = 1;
   
    end
    
    if isnan(cal2)
      applytrend = input('SHOULD GENERAL TREND FOR CONDUCTIVITIES BE APPLIED? [y/n]','s');  
      if strcmp(applytrend,'y')
        cal2   =  cal1+Ctrend;
      else
        cal2   =  cal1+Ctrend*0;
      end  
      warn = 2;
    end  

   if 0 
    if isnan(cal1)
       cal1   = cal2-Ctrend;
         warn = 1;
       disp(['no pre-cruise cond. cal. found for # ',num2str(serial0),':  GENERAL TREND APPLIED'])
    end
    if isnan(cal2)
      cal2   =  cal1+Ctrend;
        warn = 2;
       disp(['no post-cruise cond. cal. found for # ',num2str(serial0),':  GENERAL TREND APPLIED'])
    end  
   end % if 0
   
   
   %%%JC: Apply trend or average 
    
    if keep == 0
        applytrendc = input('SHOULD AVERAGE OF CONDUCTIVITY COEFFICIENTS BE APPLIED? [y/n]','s');
      if strcmp(applytrendc,'y')
        cal1   =  (cal1+cal2)/2;
        cal2   =  cal1;
      else
        cal1   =  cal1;
        cal2   =  cal2;
      end
      warn = 3;
    end
      
    %%%JC
    corrC    =  cal1 + (cal2 - cal1)/max(jd0)*jd0; 
    
    if isempty(find(~isnan(corrC)))
      disp(['no conduct. calibration found for serial number ',num2str(serial0)])
      skipC    =  input('skip (0) or save cond. data without calibration(1) ')
      corrC    =  0; 
    end    
  end  
    
  if isempty(tinI)
     disp(['no temp. calibration entry found for serial number ',num2str(serial0)])
     skipT    =  input('skip (0) or save data without calibration(1) ');
     corrT    =  0;
  else
      
    cal1comment = preTcomment(tinI); 
    cal2comment = postTcomment(tinI);
    cal1        =  preT(tinI);
    cal2        =  postT(tinI); 
    disp('   ')
    disp(['Temperature pre-cruise calibration: ',num2str(cal1)])
    disp(['Comment: ',cal1comment{1}])
    disp(['Temperature post-cruise calibration: ',num2str(cal2)])
    disp(['Comment: ',cal2comment{1}])
    disp('  ')  
    keep = input('Type 0, 1 or 2 if you want to use both, the 1st or the 2nd coefficient ');
    disp('  ')
    if keep == 1
      postT(tinI) = NaN;
      disp('Only using the pre cruise calibration coefficient')
    elseif keep == 2
      preT(tinI) = NaN;
      disp('Only using the post cruise calibration coefficient')
    elseif keep == 0
      disp('Using both calibration coefficients')
    else
      disp(['Choice ',num2str(keep),' not defined'])
      break
    end
        
    cal1     =  preT(tinI);
    cal2     =  postT(tinI);       
    if isnan(cal1)
      applytrend = input('SHOULD GENERAL TREND FOR TEMPERATURES BE APPLIED? [y/n]','s');
      if strcmp(applytrend,'y')
        cal1   =  cal2-Ttrend;
      else
        cal1   =  cal2-Ttrend*0;
      end    
      warn = 1;
   
    end
    
    if isnan(cal2)
      applytrend = input('SHOULD GENERAL TREND FOR TEMPERATURES BE APPLIED? [y/n]','s');  
      if strcmp(applytrend,'y')
        cal2   =  cal1+Ttrend;
      else
        cal2   =  cal1+Ttrend*0;
      end  
      warn = 2;
    end  
    
    %%%JC: Apply trend or average 
    
    if keep == 0
        applytrendt = input('SHOULD AVERAGE OF TEMPERATURE COEFFICIENTS BE APPLIED? [y/n]','s');
      if strcmp(applytrendt,'y')
        cal1   =  (cal1+cal2)/2;
        cal2   =  cal1;
      else
        cal1   =  cal1;
        cal2   =  cal2;
      end
      warn = 3;
    end
      
    %%%JC
    
    corrT    =  cal1 + (cal2 - cal1)/max(jd0)*jd0; 
    if isempty(find(~isnan(corrT)))
      disp(['no temp. calibration found for serial number ',num2str(serial0)])
      skipT    =  input('skip (0) or save temp. data without calibration(1) ')
      corrT    =  0; 
    end    

  end

  if strcmp(p_exist,'y')
    if isempty(pinI)
        disp(['no pressure calibration found for serial number ',num2str(serial0)])
        skipP    =  input('skip (0) or save data without calibration(1) ');
        corrP    =  0;
    else
    
      cal1comment = prePcomment(pinI); 
      cal2comment = postPcomment(pinI);
      cal1        =  preP(pinI);
      cal2        =  postP(pinI); 
      
      disp(' ')
      disp(['Pressure precruise calibration: ',num2str(cal1)])
      disp(['Comment: ',cal1comment{1}])
      disp(['Pressure postcruise calibration: ',num2str(cal2)])
      disp(['Comment: ',cal2comment{1}])
      disp(' ')
      keep = input('Type 0, 1 or 2 if you want to use both, the 1st or the 2nd coefficient ');
      disp(' ')
      if keep == 1
          postP(pinI) = NaN;
          disp('Only using the pre cruise calibration coefficient')
      elseif keep == 2
          preP(pinI) = NaN;
          disp('Only using the post cruise calibration coefficient')
      elseif keep == 0
          disp('Using both calibration coefficients')
      else
          disp(['Choice ',num2str(keep),' not defined'])
          break
      end
      disp(' ')
      cal1     =  preP(pinI);
      cal2     =  postP(pinI); 
      
      if isnan(cal1)
      applytrend = input('SHOULD GENERAL TREND FOR PRESSURES BE APPLIED? [y/n]','s');
      if strcmp(applytrend,'y')
        cal1   =  cal2-Ptrend;
      else
        cal1   =  cal2-Ptrend*0;
      end    
      warn = 1;
   
    end
    
    if isnan(cal2)
      applytrend = input('SHOULD GENERAL TREND FOR PRESSURES BE APPLIED? [y/n]','s');  
      if strcmp(applytrend,'y')
        cal2   =  cal1+Ptrend;
      else
        cal2   =  cal1+Ptrend*0;
      end  
      warn = 2;
    end  

      if 0
      if isnan(cal1)
        cal1   =  cal2-Ptrend;
          warn = 1;
         disp(['no pre-cruise pres. cal. found for # ',num2str(serial0),':  GENERAL TREND APPLIED'])
      end
      if isnan(cal2)
        cal2   =  cal1+Ptrend;
        disp(['no post-cruise pres. cal. found for # ',num2str(serial0),':  GENERAL TREND APPLIED'])
       warn = 2;
      end 
      end % if 0%%%JC: Apply trend or average 
    
    if keep == 0
        applytrendp = input('SHOULD AVERAGE OF PRESSURE COEFFICIENTS BE APPLIED? [y/n]','s');
      if strcmp(applytrendp,'y')
        cal1   =  (cal1+cal2)/2;
        cal2   =  cal1;
      else
        cal1   =  cal1;
        cal2   =  cal2;
      end
      warn = 3;
    end
      
    %%%JC
    
      corrP    =  cal1 + (cal2 - cal1)/max(jd0)*jd0; 
      if isempty(find(~isnan(corrP)))
        disp(['no pressure calibration found for serial number ',num2str(serial0)])
        skipP    =  input('skip (0) or save pressure data without calibration(1) ');
        corrP    =  0; 
    end    

    end 
   
    pn       = p - corrP;
  end

  tn       = t - corrT;      
  cn       = c - corrC;

  tn(spikeT) = dum;
  cn(spikeC) = dum;
  pn(spikeP) = dum;

  figure(1)
  
  subplot(1,sp,1)
  hold off
  ii = find(tn>dum);
  plot(jd0(ii),t(ii),'k')
  hold on
  plot(jd0(ii),tn(ii),'r')
  grid on;  title(['temperature sn',num2str(serial0)]);xlim(xli);
  
  subplot(1,sp,2)
  hold off
  ii = find(cn>dum);
  plot(jd0(ii),c(ii),'k')
  hold on
  plot(jd0(ii),cn(ii),'r')
  grid on;  title('conductivity');xlim(xli);

  if strcmp(p_exist,'y')
    subplot(1,sp,sp-1)
    hold off
    ii = find(pn>dum);
    plot(jd0(ii),p(ii),'k')
    hold on
    plot(jd0(ii),pn(ii),'r')
    grid on; title('pressure'); xlim(xli);
  end

  subplot(1,sp,sp)
  hold off
  if warn == 0
    plot(jd0([1 end]),corrT([1 end])*1000,'o-')
    hold on
    plot(jd0([1 end]),corrC([1 end])*1000,'ro-')
    legend('T drift','C drift',1); xlim(xli);
    if strcmp(p_exist,'y')
      plot(jd0([1 end]),corrP([1 end]),'go-')
      legend('T drift','C drift','P drift',1)
    end
  elseif warn ==1
    pl1=plot(jd0([1 end]),corrT([1 end])*1000,'--',jd0(end),corrT(end)*1000,'o');
    hold on    
    pl2=plot(jd0([1 end]),corrC([1 end])*1000,'r--',jd0(end),corrC(end)*1000,'ro');
    legend([pl1(1) pl2(1)],'T drift','C drift',1)
    if strcmp(p_exist,'y')
      pl3=plot(jd0([1 end]),corrP([1 end]),'g-',jd0(end),corrP(end),'go');
      legend([pl1(1) pl2(1) pl3(1)],'T drift','C drift','P drift',1)
    end
  elseif warn ==2
    pl1=plot(jd0([1 end]),corrT([1 end])*1000,'--',jd0(1),corrT(1)*1000,'o');
    hold on    
    pl2=plot(jd0([1 end]),corrC([1 end])*1000,'r--',jd0(1),corrC(1)*1000,'ro');
    legend([pl1(1) pl2(1)],'T drift','C drift',1)
    if strcmp(p_exist,'y')
      pl3=plot(jd0([1 end]),corrP([1 end]),'g-',jd0(1),corrP(1),'go');
      legend([pl1(1) pl2(1) pl3(1)],'T drift','C drift','P drift',1)
    end
  elseif warn ==3
      plot(jd0([1 end]),corrT([1 end])*1000,'o-')
    hold on
    plot(jd0([1 end]),corrC([1 end])*1000,'ro-')
    legend('T drift','C drift',1); xlim(xli);
    if strcmp(p_exist,'y')
      plot(jd0([1 end]),corrP([1 end]),'go-')
      legend('T drift','C drift','P drift',1)
    end
  end 
  grid on 
 
  c3515   = sw_c3515;
  
  ii = find(cn>dum & pn>dum);     
  sn = sw_salt(cn(ii)/c3515,tn(ii)*t90_68,pn(ii));
 
  figure(6);clf; hold on
  
  plot(jd(ii)-jd(1),sn)
  ylabel('Salinity')
  title('Salinity')
  
  %%JC 
     sss=sw_salt(c/c3515,t*t90_68,p);
     figure(8);clf; hold on
  
     plot(sss,t,'.')
     xlabel('Salinity')
     ylabel('Temperature')
  %%JC
  
  %%%%%%%%%%%%%% --- 2) conductivity pressure correction

acc_cpcor = input('Is a conductivity pressure correction required (see Fig. 1)? y/n ','s');

if strcmp(acc_cpcor,'y')
  pref = input('Insert reference pressure [dbar] ');
  invalcpcor = input('Insert time interval where correction is required, e.g. [3 56] denotes days 3-56 ');
  dumI = find(cn == dum);
  
  for i = 1 : length(invalcpcor)/2,  
    corI = find(jd0>= invalcpcor(i*2-1) & jd0 <= invalcpcor(i*2));    
    cn(corI) = mc_concorr(cn(corI),pref,pn(corI));
  end
  cn(dumI) = dum;
  figure(6)
    sn = sw_salt(cn(ii)/c3515,tn(ii)*t90_68,pref*ones(length(ii),1));
    plot(jd(ii)-jd(1),sn,'r')
    grid on
    legend('raw','P corr.')

elseif strcmp(acc_cpcor,'n')
  disp('No pressure conductivity correction applied')
  
elseif ~strcmp(acc_cpcor,'y') & ~strcmp(acc_cpcor,'n')
  disp('Answer not defined - no pressure conductivity correction applied')  
  acc_cpcor = 'n';
  
end




  %%%%%%%%% 3)  pressure drift removal ---------------

acc_drift = input('Does the pressure record require drift removal (see Fig. 1)?  y/n ','s');

if strcmp(acc_drift,'y')

  [coef,fit]= exp_lin_fit2(jd,pn,[1 1 1 1]);

  figure(3);clf;hold on

  plot(jd0,pn)
  plot(jd0,fit,'r','Linewidth',2)
  grid on
  if warn == 1
    pp = pn - fit + fit(end);
  elseif warn == 2
    pp = pn - fit + fit(1);
  else 
    pp = pn - fit + mean(fit);
  end   
  so = sw_salt(cn(ii)/c3515,tn(ii)*t90_68,pn(ii));
  sn = sw_salt(cn(ii)/c3515,tn(ii)*t90_68,pp(ii));
  deltaS = std(sn)-std(so)
    fprintf(1,'\n\n Salinity standard deviation should decrease with dedrifted pressure \n')
  if deltaS > 0
    fprintf(1,' Salinity standard deviation has INCREASED instead  by %4.4f \n\n',deltaS)
  elseif deltaS < 0
    fprintf(1,' WARNING: Salinity standard deviation has DECREASED by %4.4f \n\n',-deltaS)
  end
  plot(jd0,pp,'g')
  
  figure(111);clf;hold on
    plot(so,tn(ii),'b.')
    plot(sn,tn(ii),'r.')
    grid on
    xlabel('S')
    ylabel('T')
    drawnow
    legend('P raw','P dedrifted')
    
  acc = input('Do you accept the the drift removal y/n ','s');

  if strcmp(acc,'y')
    disp('drift removal accepted')
    
    figure(1)
    subplot(1,sp,sp-1)
    plot(jd0(ii),pn(ii),'w')
    plot(jd0(ii),pp(ii),'r')
    pn =pp;
    figure(6)
    plot(jd(ii)-jd(1),sn,'r')
    legend('raw','P corr')
    
    
  else
    disp('drift removal dicarded')
  end    
elseif strcmp(acc_drift,'n')
  disp('No drift correction applied')
  
elseif ~strcmp(acc_drift,'y') & ~strcmp(acc_drift,'n')
  disp('Answer not defined - no drift correction carried applied')  
  acc_drift = 'n';
end    


  
  %%%%%%%----- 4)  set suspicious data to dummies -------%%%%%%%%%%
  
  invalT = 0; invalC = 0; invalP = 0;
  
  inval= input('Should temperatures in a certain time interval be set to dummies y/n \n','s');

  if inval == 'y' % temp dummies loop
    invalT= input('Insert interval, e.g. [2 4 6 9] denotes days 2-4 and days 6-9 \n');      
    while 1
      if isodd(length(invalT))
        invalT= input('Repeat entry, vector must have even number of elements \n');  
      else
        break
      end
    end 

    for i = 1 : length(invalT)/2,  
      dumI = find(jd0>= invalT(i*2-1) & jd0 <= invalT(i*2));    
      tn(dumI) = dum;
    end
    figure(1)
    subplot(1,sp,1)
    hold off
    ii = find(tn>dum);
    plot(jd0(ii),t(ii),'k')
    hold on
    plot(jd0(ii),tn(ii),'b')
    title('temperature');xlim(xli);
  end %  temp. dummies loop 

  inval= input('Should conductivities in a certain time interval be set to dummies y/n \n','s');
  
  if inval == 'y' % cond dummies loop

    invalC= input('Enter interval, e.g. [2 4 6 9] denotes days 2-4 and days 6-9 \n');      
    while 1
      if isodd(length(invalC))
        invalC= input('Repeat entry, vector must have even number of elements \n')  
      else
        break
      end
    end 

    for i = 1 : length(invalC)/2,  
      dumI = find(jd0>= invalC(i*2-1) & jd0 <= invalC(i*2));    
      cn(dumI) = dum;
    end
    figure(1)
    subplot(1,sp,2)
    hold off
    ii = find(cn>dum);
    plot(jd0(ii),c(ii),'k')
    hold on
    plot(jd0(ii),cn(ii),'r')
    title('conductivity');xlim(xli);
  end %  cond. dummies loop 

  if ~isempty(find(~isnan(p)))
    inval= input('Should pressures in a certain time interval be set to dummies y/n \n','s');

    if inval == 'y' % cond dummies loop
      invalP= input('Enter interval, e.g. [2 4 6 9] denotes days 2-4 and days 6-9 \n');      

      while 1
        if isodd(length(invalP))
          inval= input('Repeat entry, interval must have even number of elements \n')      
        else
          break
        end
      end 

      for i = 1 : length(invalP)/2,  
        dumI = find(jd0>= invalP(i*2-1) & jd0 <= invalP(i*2));    
        pn(dumI) = dum;
      end
      figure(1)
      subplot(1,sp,3)
      hold off
      ii = find(pn>dum);
      plot(jd0(ii),p(ii),'k')
      hold on
      plot(jd0(ii),pn(ii),'r')
      title('pressure');xlim(xli);
    end %  cond. dummies loop 
  end

 
%%%%%%%%%%%%%% 5) interactive despiking  %%%%%%%%%%%%%%%%%%
  
  
  c3515   = sw_c3515;

  val = find(cn>dum & pn>dum);  


  if isempty(val)
    val = find(cn>dum);
    sn = sw_salt(cn(val)/c3515,tn(val)*t90_68,pref*ones(length(val),1));
  else    
    sn = sw_salt(cn(val)/c3515,tn(val)*t90_68,pn(val));
  end 
 
  figure(111);hold off
  if ~isempty(find(pn>dum))
      %%plot(sn,theta(pn(val),tn(val),sn,median(pn(val))),'.k') %TK
      plot(sn,sw_ptmp(sn,tn(val),pn(val),median(pn(val))),'.k')
  else     
   
    plot(sn,tn(val),'.k')
  end 
  grid on
  

    if exist([external_ctd,external_ctd_file]) == 2
      eval(['load ',external_ctd,external_ctd_file])
      for cnt = 1 :length(ctd_prof)
        dis(cnt) = dist2([ lt ctd_lat(cnt)],[ln ctd_lon(cnt)]);
      end
  
      near = find(dis<200e3);
   
      [ctd_p,ctd_t,ctd_s] = rodbload(['cruise:d279:ctd:[',num2str(near),']'],'p:t:s');
      hold on
      xli = get(gca,'Xlim');
      yli = get(gca,'Ylim');
      if ~isempty(find(pn>dum))
        %%plot(ctd_s,theta(ctd_p,ctd_t,ctd_s,median(pn(val))),'g')
        plot(ctd_s,sw_ptmp(ctd_s,ctd_t,ctd_p,median(pn(val))),'g')
      else     
        plot(ctd_s,ctd_t,'g')
 
      end 

   
      xlim(xli);ylim(yli);
    end

    
  ELIM = []; 
  while 1   
    cut = input('Want to exclude spikes from T/S plot interactively y/n ','s');
   
    if strcmp(cut,'y') 
      fprintf(1,'Define an area that covers spikes by clicking on graph \n') 
      [x,y] = ginput;
      x = [x ;x(1)];
      y = [y ;y(1)];
      %elim = find(sn>min(x) & sn<max(x) & tn(val)>min(y) &tn(val)<max(y));
      elim = find(inpolygon(sn,tn(val),x,y) == 1);
     
      ELIM = [ELIM;elim];
      hold on
      if ~isempty(find(pn>dum))
         %plot(sn(elim),theta(pn(val(elim)),tn(val(elim)),sn(elim),median(pn(val))),'.w') 
         plot(sn(elim),sw_ptmp(sn(elim),tn(val(elim)),pn(val(elim)),median(pn(val))),'.w') 

      else     
   
         plot(sn(elim),tn(val(elim)),'.w')
      end 
      
     
    else
      %%tn(val(ELIM)) = dum; 
      figure(1)
      subplot(1,sp,2)
      
      cn(val(ELIM)) = dum;
      sn(ELIM)      = dum;
      ii = find(cn>dum);
      plot(jd0(ii),cn(ii),'g')
      fprintf(1,'%d data points eliminated \n\n\n',length(ELIM)) 
      break
    end    
  end % end while
  
    % automatic de-spiking
    
    val   = find(cn> dum &pn>dum);
    if isempty(val)
      val = find(cn>dum);
      sn  = sw_salt(cn(val)/c3515,tn(val)*t90_68,pref*ones(length(val),1));
    else    
      sn =  sw_salt(cn(val)/c3515,tn(val)*t90_68,pn(val));
    end 
       ELIM2 = [];
    if ~isempty(val) 
      Tlim  = [min(tn(val)) max(tn(val))];
      dTlim = diff(Tlim); 
      Tstep = 15;
      Tgrid = linspace(Tlim(1),Tlim(2),Tstep);
   
   
    
      for i = 1 : Tstep -1
        ii     = find(tn(val)>=Tgrid(i) & tn(val)<=Tgrid(i+1));
        if ii < 3
          ssd(i) = ssd(i-1);  
        else
          ssd(i) = std(sn(ii));
        end  
        
        smd(i) = median(sn(ii));
        elim  =          find(sn(ii) > (smd(i)+6*ssd(i)) | sn(ii) < (smd(i) - 6*ssd(i)));
        ELIM2 = [ELIM2 ii(elim)'];
      end
      Tgrid = mean([Tgrid(1:end-1);Tgrid(2:end)]);
      figure(111) 
      hold on
      plot(smd+6*ssd,Tgrid,'m--','Linewidth',2)
      plot(smd-6*ssd,Tgrid,'m--','Linewidth',2)
    
      auto = input('Do you want to exclude all the values outside the 6 sigma area y/n ','s');
      if strcmp(auto,'y')
        disp(['Automatic despiking accepted - ',num2str(length(ELIM2)),' values discarded']);  
        cn(val(ELIM2)) = dum;
        plot(sn(ELIM2),tn(val(ELIM2)),'w.')
      elseif strcmp(auto,'n')
        disp('Automatic despiking rejected')    
        ELIM2 = [];
      else 
        disp('Answer not defined - Automatic despiking rejected')
        ELIM2 = [];
      end  
    end
 %% ---  replace NaN by dummy
 
 ii     = find(isnan(pn));
 pn(ii) = dum;
 ii     = find(isnan(cn));
 cn(ii) = dum;
 ii     = find(isnan(tn));
 tn(ii) = dum;
 
 %%%JC
  
 %eval(['print -dpsc ',mcfile_out,'.ps'])
 
 %%%JC
 
 
%%%%%%%%%%% ---- 6) save data ------%%%%%%%%%%% 

  if skipT == 1 & skipC == 1 & skipP == 1    
    if isempty(find(~isnan(p)))
      dat = [yy mm dd hh tn cn];   
      rodbsave(mcfile_out,head,fort,moo,serial0,wd,id,sd,st,ed,et,lt,ln,cols,dat)
    else
      dat = [yy mm dd hh tn cn pn];
      rodbsave(mcfile_out,head,fortp,moo,serial0,wd,id,sd,st,ed,et,lt,ln,colsp,dat) % instr with pressure option
    end
    fprintf(1,[mcfile_out,' has been saved\n\n\n'])
  else  
    fprintf(1,[mcfile_out,' has NOT been saved\n\n\n'])
  end 
 
 
  
 %%% ---- Text Output -------------------------------------
 
 fidtxt  =  fopen([mcfile_out,'.txt'],'w');
 
 fprintf(fidtxt,'Microcat_apply_cal.m: \n');
 fprintf(fidtxt,'Date    : %s \n',date);
 fprintf(fidtxt,'Operator: %s \n',operator);
 fprintf(fidtxt,'Input file : %s \n',mcfile);
 fprintf(fidtxt,'Output file: %s \n',mcfile_out);
 fprintf(fidtxt,'Variable            | pre-cruise | post-cruise \n');
 fprintf(fidtxt,'Conductivity [mS/cm]:  %5.4f        %5.4f \n',preC(cinI),postC(cinI));
 fprintf(fidtxt,'Temperature  [K]    :  %5.4f        %5.4f \n',preT(tinI),postT(tinI));
 fprintf(fidtxt,'Pressure     [dbar] :  %5.4f        %5.4f \n',preP(pinI),postP(pinI));
 if exist('applytrendc')
 fprintf(fidtxt,'Average conductivity applied? %s\n',applytrendc);
 end
 if exist('applytrendt')
 fprintf(fidtxt,'Average temperature applied? %s\n',applytrendt);
 end
 if exist('applytrendp')
 fprintf(fidtxt,'Average pressure applied? %s\n',applytrendp);
 end
 fprintf(fidtxt,'Pressure drift removal? %s\n',acc_drift);
 fprintf(fidtxt,'Conductivity pressure correction redone? %s\n',acc_cpcor);
 fprintf(fidtxt,'Skipped conductivity intervals: [%s]\n',num2str(invalC));
 fprintf(fidtxt,'Skipped temperature intervals : [%s]\n',num2str(invalT));
 fprintf(fidtxt,'Skipped pressure intervals    : [%s]\n',num2str(invalP));
 fprintf(fidtxt,'Number of additional C points skipped interactively: [%d]\n',length(ELIM));
 fprintf(fidtxt,'Number of additional C points skipped automaticly: [%d]\n',length(ELIM2));

end

