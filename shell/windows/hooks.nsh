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

; Asks a running Ringkeeper to quit (it stops its work the way Quit does) and
; waits until its program and its Python are no longer in use.
!macro RK_QUIT_AND_WAIT
  ${If} ${FileExists} "$INSTDIR\${MAINBINARYNAME}.exe"
    DetailPrint "Closing Ringkeeper if it's running"
    ExecWait '"$INSTDIR\${MAINBINARYNAME}.exe" --quit'
    StrCpy $R8 0
    ${Do}
      ClearErrors
      ${If} ${FileExists} "$INSTDIR\python\*.*"
        Rename "$INSTDIR\python" "$INSTDIR\python.in-use-check"
        ${IfNot} ${Errors}
          Rename "$INSTDIR\python.in-use-check" "$INSTDIR\python"
        ${EndIf}
      ${EndIf}
      ${IfNot} ${Errors}
        nsis_tauri_utils::FindProcess "${MAINBINARYNAME}.exe"
        Pop $R9
        ${IfThen} $R9 <> 0 ${|} ${Break} ${|}
      ${EndIf}
      ; Quit gives running work a minute to stop.
      ${If} $R8 >= 90
        MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "Ringkeeper is still running. Quit it from its tray icon, then click Retry." /SD IDCANCEL IDRETRY +2
        Abort "Ringkeeper is still running."
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
