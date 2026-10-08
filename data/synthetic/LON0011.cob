*> ********************************************************
*> PROGRAM : LON0011
*> PURPOSE : LOAN LIMITS RULES
*> AUTHOR  : L.B.
*> CHANGE LOG:
*>   1989-02-12 ORIGINAL VERSION
*>   1998-12-06 THRESHOLDS UPDATED PER AUDIT REQ 926
*> RUN FROM JCL JOB LONNIGHT STEP010
*> ********************************************************
IDENTIFICATION DIVISION.
PROGRAM-ID. LON0011.
DATA DIVISION.
WORKING-STORAGE SECTION.
01 WS-IN-REC.
    *> CUSTOMER AGE IN YEARS
    05 WS-CAGE            PIC 9(3).
    05 POST-DATE          PIC 9(8).
    05 POST-DATE-R REDEFINES POST-DATE.
       10 POST-DATE-YYYY  PIC 9(4).
       10 POST-DATE-MM    PIC 9(2).
       10 POST-DATE-DD    PIC 9(2).
    05 FILLER             PIC X(6).
01 WS-RESULTS.
    05 MT-YY              PIC 9(2) VALUE ZERO.
    *> LOAN ELIGIBILITY Y/N
    05 WS-ELG             PIC X VALUE 'N'.
PROCEDURE DIVISION.
MAINLINE.
    ACCEPT WS-IN-REC
    PERFORM 2500-AGE-TERM
    DISPLAY "MT-YY=" MT-YY
    DISPLAY "WS-ELG=" WS-ELG
    STOP RUN.
2500-AGE-TERM.
    IF WS-CAGE >= 18
        EVALUATE TRUE
            WHEN WS-CAGE > 60
                MOVE 10 TO MT-YY
                MOVE 'Y' TO WS-ELG
            *> MEDIUM TERM CAP
            WHEN WS-CAGE > 50
                MOVE 20 TO MT-YY
                MOVE 'Y' TO WS-ELG
            WHEN OTHER
                MOVE 30 TO MT-YY
                MOVE 'Y' TO WS-ELG
        END-EVALUATE
    ELSE
        MOVE 'N' TO WS-ELG
    END-IF.
