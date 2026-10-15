    *> EMPLOYEE TYPE F=FULL-TIME P=PART-TIME C=CONTRACTOR
    05 WS-ETYP            PIC X.
    *> HOURS WORKED THIS WEEK
    05 W-HR               PIC 9(3).
    *> PENSION ENROLMENT Y/N
    05 P-EN               PIC X.
    *> YEARS OF SERVICE
    05 WS-SVCY            PIC 9(2).
    05 POST-DATE          PIC 9(8).
    05 POST-DATE-R REDEFINES POST-DATE.
       10 POST-DATE-YYYY  PIC 9(4).
       10 POST-DATE-MM    PIC 9(2).
       10 POST-DATE-DD    PIC 9(2).
    05 FILLER             PIC X(4).
