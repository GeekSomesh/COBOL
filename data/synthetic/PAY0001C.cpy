    *> EMPLOYEE TYPE F=FULL-TIME P=PART-TIME C=CONTRACTOR
    05 ET-CD              PIC X.
    *> HOURS WORKED THIS WEEK
    05 HW-HH              PIC 9(3).
    *> GROSS PAY FOR THE PERIOD
    05 P-GR               PIC 9(7)V99.
    05 PROC-DATE          PIC 9(8).
    05 PROC-DATE-R REDEFINES PROC-DATE.
       10 PROC-DATE-YYYY  PIC 9(4).
       10 PROC-DATE-MM    PIC 9(2).
       10 PROC-DATE-DD    PIC 9(2).
    05 FILLER             PIC X(4).
