Option Explicit

Dim args, sourcePath, outputDir, fso, app, deck, slide, meta
Set args = WScript.Arguments

If args.Count <> 2 Then
    WScript.Echo "Usage: cscript export_pptx.vbs <source.pptx> <output-dir>"
    WScript.Quit 2
End If

sourcePath = args(0)
outputDir = args(1)
Set fso = CreateObject("Scripting.FileSystemObject")

If Not fso.FileExists(sourcePath) Then
    Err.Raise vbObjectError + 100, , "PowerPoint file not found: " & sourcePath
End If

If Not fso.FolderExists(outputDir) Then
    fso.CreateFolder(outputDir)
End If

On Error Resume Next
Set app = CreateObject("PowerPoint.Application")
If Err.Number <> 0 Then
    WScript.Echo "CREATE_ERROR " & Err.Number & " " & Err.Description
    WScript.Quit 3
End If

Set deck = app.Presentations.Open(sourcePath, -1, -1, 0)
If Err.Number <> 0 Then
    WScript.Echo "OPEN_ERROR " & Err.Number & " " & Err.Description
    app.Quit
    WScript.Quit 4
End If

Set meta = fso.CreateTextFile(fso.BuildPath(outputDir, "deck-metadata.txt"), True, True)
meta.WriteLine "slide_count=" & deck.Slides.Count
meta.WriteLine "slide_width_points=" & deck.PageSetup.SlideWidth
meta.WriteLine "slide_height_points=" & deck.PageSetup.SlideHeight

For Each slide In deck.Slides
    Err.Clear
    slide.Export fso.BuildPath(outputDir, "slide-" & slide.SlideIndex & ".png"), "PNG", 1600, 900
    meta.WriteLine "slide=" & slide.SlideIndex & vbTab & "shape_count=" & slide.Shapes.Count
    If Err.Number <> 0 Then
        meta.WriteLine "export_error=" & Err.Number & vbTab & Err.Description
        Err.Clear
    End If
Next

meta.Close
deck.Close
app.Quit
On Error GoTo 0

WScript.Echo fso.BuildPath(outputDir, "deck-metadata.txt")
