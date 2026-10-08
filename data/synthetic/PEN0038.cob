*> ********************************************************
*> PROGRAM : PEN0038
*> PURPOSE : PENALTY ASSESSMENT RULES
*> AUTHOR  : M.S.
*> CHANGE LOG:
*>   1989-03-10 ORIGINAL VERSION
*>   2003-11-08 THRESHOLDS UPDATED PER AUDIT REQ 994
*> RUN FROM JCL JOB PENNIGHT STEP060
*> ********************************************************
IDENTIFICATION DIVISION.
PROGRAM-ID. PEN0038.
DATA DIVISION.
WORKING-STORAGE SECTION.
01 IN-RECORD.
    *> FIRST LATE PAYMENT Y/N
    05 WS-FOF             PIC X.
    *> TIER G=GOLD P=PLATINUM S=SILVER B=BASIC
    05 WS-TIER            PIC X.
    *> DAYS PAYMENT IS LATE
    05 D-PD               PIC 9(3).
    05 PROC-DATE          PIC 9(8).
    05 PROC-DATE-R REDEFINES PROC-DATE.
       10 PROC-DATE-YYYY  PIC 9(4).
       10 PROC-DATE-MM    PIC 9(2).
       10 PROC-DATE-DD    PIC 9(2).
    05 FILLER             PIC X(6).
01 WS-OUT-REC.
    *> PENALTY WAIVER FLAG
    05 P-WF               PIC X VALUE 'N'.
    05 WS-PEN             PIC 9(5)V99 VALUE ZERO.
PROCEDURE DIVISION.
A000-MAIN.
    ACCEPT IN-RECORD
    PERFORM WAIVE-PENALTY
    DISPLAY "P-WF=" P-WF
    DISPLAY "WS-PEN=" WS-PEN
    STOP RUN.
WAIVE-PENALTY.
    IF WS-FOF = 'Y'
        *> PREMIUM FIRST-OFFENCE WAIVER
        IF WS-TIER = 'G' OR WS-TIER = 'P'
            MOVE 'Y' TO P-WF
            MOVE 0 TO WS-PEN
        ELSE
            *> GRACE PERIOD WAIVER
            IF D-PD <= 7
                MOVE 'Y' TO P-WF
                MOVE 0 TO WS-PEN
            END-IF
        END-IF
    END-IF.
