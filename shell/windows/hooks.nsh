; Ringkeeper's additions to Tauri's NSIS installer (see installer.nsi, which
; marks the few places it calls these). Macros, not functions: Tauri includes
; this file before it defines PRODUCTNAME and the rest, and a macro expands
; where it's used.
;
; Updates and reinstalls set the old version's files aside first and put them
; back if the install fails, so a failed update leaves the old version
; working. The data folder (%LOCALAPPDATA%\Ringkeeper) is never touched here,
; except by an uninstall with "Also delete my data" ticked.

!define RK_PREVIOUS "previous-version"
Var RkPrevious

; Windows paths stop at 260 characters, and the bundled Python's deepest file
; is up to 165 below the install folder (shell/scripts/bundle.py checks).
!define RK_MAX_INSTDIR 90

!macro RK_CHECK_INSTDIR
  StrLen $R9 "$INSTDIR"
  ${If} $R9 > ${RK_MAX_INSTDIR}
    MessageBox MB_ICONEXCLAMATION "This folder's path is too long for Ringkeeper's files. Choose a folder whose path is at most ${RK_MAX_INSTDIR} characters, such as the one suggested." /SD IDOK
    Abort
  ${EndIf}
!macroend

; Sets $R7 to the full path of a program running from the install folder
; (Ringkeeper, its Python or its Git), or "" if none is. By path, not by name:
; a ringkeeper.exe running from anywhere else (a developer's copy) isn't this
; install's. Nor is this installer itself: an uninstall over a reinstall runs
; $INSTDIR\uninstall.exe in place. The installer is 32-bit; it sees 64-bit
; programs' paths too.
!macro RK_FIND_RUNNING
  Push $4
  Push $5
  Push $6
  Push $7
  Push $8
  Push $9
  StrCpy $R7 ""
  System::Call 'kernel32::GetCurrentProcessId() i .r4'
  System::Call 'kernel32::CreateToolhelp32Snapshot(i 2, i 0) p .r6'  ; the processes
  ${If} $6 <> -1
    ; PROCESSENTRY32W
    System::Call '*(&l4, i 0, i 0, p 0, i 0, i 0, i 0, i 0, i 0, &w260 "") p .r7'
    System::Call 'kernel32::Process32FirstW(p r6, p r7) i .r8'
    ${DoWhile} $8 <> 0
      System::Call '*$7(i, i, i .r9)'
      ${If} $9 = $4
        StrCpy $9 0
      ${Else}
        ; PROCESS_QUERY_LIMITED_INFORMATION
        System::Call 'kernel32::OpenProcess(i 0x1000, i 0, i r9) p .r9'
      ${EndIf}
      ${If} $9 <> 0
        System::Call 'kernel32::QueryFullProcessImageNameW(p r9, i 0, w .r5, *i ${NSIS_MAX_STRLEN}) i .r8'
        System::Call 'kernel32::CloseHandle(p r9)'
        StrLen $9 "$INSTDIR\"
        StrCpy $9 $5 $9
        ${If} $8 <> 0
        ${AndIf} $9 == "$INSTDIR\"  ; == ignores case, as Windows paths do
          StrCpy $R7 $5
          ${Break}
        ${EndIf}
      ${EndIf}
      System::Call 'kernel32::Process32NextW(p r6, p r7) i .r8'
    ${Loop}
    System::Free $7
    System::Call 'kernel32::CloseHandle(p r6)'
  ${EndIf}
  Pop $9
  Pop $8
  Pop $7
  Pop $6
  Pop $5
  Pop $4
!macroend

; Asks a running Ringkeeper to quit (it stops its work the way Quit does) and
; waits until nothing runs from the install folder and its Python folder can
; be replaced. If that takes too long, says what's still there.
!macro RK_QUIT_AND_WAIT
  ${If} ${FileExists} "$INSTDIR\${MAINBINARYNAME}.exe"
    DetailPrint "Closing Ringkeeper if it's running"
    ExecWait '"$INSTDIR\${MAINBINARYNAME}.exe" --quit'
    StrCpy $R8 0
    ${Do}
      !insertmacro RK_FIND_RUNNING
      StrCpy $R6 ""  ; a folder in use (testing ${Errors} clears it, so not that)
      ${If} $R7 == ""
      ${AndIf} ${FileExists} "$INSTDIR\python\*.*"
        ClearErrors
        Rename "$INSTDIR\python" "$INSTDIR\python.in-use-check"
        ${If} ${Errors}
          StrCpy $R6 "$INSTDIR\python"
        ${Else}
          Rename "$INSTDIR\python.in-use-check" "$INSTDIR\python"
        ${EndIf}
      ${EndIf}
      ${If} $R7 == ""
      ${AndIf} $R6 == ""
        ${Break}
      ${EndIf}
      ; Quit gives running work a minute to stop.
      ${If} $R8 >= 90
        ${If} $R7 != ""
          StrCpy $R9 "Ringkeeper is running from $R7."
          StrCpy $R6 "Quit it from its tray icon, then click Retry."
        ${Else}
          StrCpy $R9 "Another program is using $R6."
          StrCpy $R6 "Close it (a terminal or File Explorer window open there, perhaps), then click Retry."
        ${EndIf}
        DetailPrint "$R9"
        MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$R9$\n$\n$R6" /SD IDCANCEL IDRETRY +2
        Abort "$R9"
        StrCpy $R8 0
      ${EndIf}
      Sleep 1000
      IntOp $R8 $R8 + 1
    ${Loop}
  ${EndIf}
!macroend

!macro RK_SET_ASIDE_PART part
  ${If} ${FileExists} "$INSTDIR\${part}"
    ClearErrors
    Rename "$INSTDIR\${part}" "$RkPrevious\${part}"
    ${If} ${Errors}
      Abort "Couldn't replace $INSTDIR\${part}: is it open in another program?"
    ${EndIf}
  ${EndIf}
!macroend

!macro RK_RESTORE_PART part
  ${If} ${FileExists} "$RkPrevious\${part}"
    RMDir /r "$INSTDIR\${part}"
    Delete "$INSTDIR\${part}"
    Rename "$RkPrevious\${part}" "$INSTDIR\${part}"
  ${EndIf}
!macroend

; Before installing over an existing copy: quit it, then move its files (only
; Ringkeeper's own) into previous-version, so nothing stale stays behind. A
; previous-version left from an update the app hasn't cleaned up yet goes first.
!macro RK_SET_ASIDE
  StrCpy $RkPrevious ""
  !insertmacro RK_CHECK_INSTDIR
  ${If} ${FileExists} "$INSTDIR\${MAINBINARYNAME}.exe"
    !insertmacro RK_QUIT_AND_WAIT
    StrCpy $RkPrevious "$INSTDIR\${RK_PREVIOUS}"
    RMDir /r "$RkPrevious"
    CreateDirectory "$RkPrevious"
    !insertmacro RK_SET_ASIDE_PART "python"
    !insertmacro RK_SET_ASIDE_PART "web"
    !insertmacro RK_SET_ASIDE_PART "mingit"
    !insertmacro RK_SET_ASIDE_PART "notices"
    !insertmacro RK_SET_ASIDE_PART "uninstall.exe"
    !insertmacro RK_SET_ASIDE_PART "${MAINBINARYNAME}.exe"
  ${EndIf}
!macroend

; The install failed: put the previous version back as it was.
!macro RK_RESTORE
  ${If} $RkPrevious != ""
    !insertmacro RK_RESTORE_PART "python"
    !insertmacro RK_RESTORE_PART "web"
    !insertmacro RK_RESTORE_PART "mingit"
    !insertmacro RK_RESTORE_PART "notices"
    !insertmacro RK_RESTORE_PART "uninstall.exe"
    !insertmacro RK_RESTORE_PART "${MAINBINARYNAME}.exe"
    RMDir /r "$RkPrevious"
  ${EndIf}
!macroend

; previous-version stays after a successful install: the new version removes
; it in the background when it starts, so the update finishes sooner.

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro RK_QUIT_AND_WAIT
  ; The master key lives in Windows Credential Manager, outside the data
  ; folder: it goes with the data, and stays with it.
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    DetailPrint "Removing Ringkeeper's key from Windows Credential Manager"
    nsExec::Exec '"$INSTDIR\python\python.exe" -I -m ringkeeper.desktop.uninstall'
    Pop $R9
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ; Files Python or the app wrote next to the installed ones.
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\web"
  RMDir /r "$INSTDIR\mingit"
  RMDir /r "$INSTDIR\notices"
  RMDir /r "$INSTDIR\${RK_PREVIOUS}"
  RMDir "$INSTDIR"
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    SetShellVarContext current
    RMDir /r "$LOCALAPPDATA\${PRODUCTNAME}"
  ${EndIf}
!macroend
