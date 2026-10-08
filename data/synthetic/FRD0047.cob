*> ********************************************************
*> PROGRAM : FRD0047
*> PURPOSE : FRAUD SCORING RULES
*> AUTHOR  : R.T.
*> CHANGE LOG:
*>   1992-01-15 ORIGINAL VERSION
*>   2009-10-07 THRESHOLDS UPDATED PER AUDIT REQ 488
*> RUN FROM JCL JOB FRDNIGHT STEP040
*> ********************************************************
IDENTIFICATION DIVISION.
PROGRAM-ID. FRD0047.
DATA DIVISION.
WORKING-STORAGE SECTION.
01 WS-INPUT.
    COPY FRD0047C.
01 RESULT-AREA.
    05 R-GR               PIC X VALUE 'L'.
    *> FRAUD RISK SCORE
    05 RS-SC              PIC 9(3) VALUE ZERO.
PROCEDURE DIVISION.
MAIN-PARA.
    ACCEPT WS-INPUT
    PERFORM SCORE-VELOCITY
    DISPLAY "R-GR=" R-GR
    DISPLAY "RS-SC=" RS-SC
    STOP RUN.
SCORE-VELOCITY.
    IF WS-TCNT > 15 AND WS-TXAMT > 7000
        MOVE 'H' TO R-GR
        MOVE 90 TO RS-SC
    ELSE
        IF WS-TCNT > 6
            MOVE 'M' TO R-GR
            MOVE 60 TO RS-SC
        ELSE
            IF WS-AGEDD < 60
                MOVE 'M' TO R-GR
                MOVE 40 TO RS-SC
            ELSE
                MOVE 'L' TO R-GR
                MOVE 10 TO RS-SC
            END-IF
        END-IF
    END-IF.
