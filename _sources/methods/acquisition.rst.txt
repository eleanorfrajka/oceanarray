Data Acquisition (Download in proprietary formats)
==================================================

This document describes the initial steps required to bring raw instrument data into a standardized processing pipeline. These steps correspond to **Stage 0** of the RAPID data processing and management.

1. Overview
-----------

Most mooring instruments output data in proprietary formats (e.g., SeaBird `.cnv`, ASCII logs, or binary files). The acquisition stage covers:

- Locating and organizing raw instrument files
- Extracting metadata from deployment records
- Converting proprietary formats into a structured and inspectable format

This stage includes *no scientific processing*, and is typically done at sea as soon as a mooring is recovered and instruments are available.


2. Download
-----------

Raw instrument data are downloaded e.g. from MicroCATs using SeaBird's SeaTerm software and saved as ASCII files.

For the newer 6000 series of MicroCATs it was discovered that the older version of
the SeaTerm software misses a line of data every time it completes a block of 220
lines. The newer SeaTermV2 software downloads the data in .xml format, which is
then converted to .cnv format using the ‘tools’ menu (see section 18.6).


**Expected inputs:**

- One file per instrument (e.g., `.cnv`, `.hex`, `.txt`, `.dat`)
- Deployment metadata (e.g., `info.yaml`)


**Reference:**
Cunningham, S.A. (2010) RRS Discovery Cruise D344, 21 Oct-18 Nov 2009. RAPID Mooring Cruise Report. Southampton, UK, National Oceanography Centre Southampton, 225pp. (National Oceanography Centre Southampton Cruise Report 51) https://nora.nerc.ac.uk/id/eprint/263915/1/nocscr051.pdf

Instrument Data Acquisition
---------------------------

This section outlines the procedures for downloading data from Seabird SBE37 SMP instruments, depending on firmware version.

Legacy Firmware (pre-3.0d)
^^^^^^^^^^^^^^^^^^^^^^^^^^

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
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^


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
