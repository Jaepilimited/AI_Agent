Option Explicit

Dim args, sourcePath, outputPath, fso, app, deck, outFile
Dim slide, shape, textValue, fontName, fontSize, fontBold, fontColor
Dim fillColor, fillTransparency, lineColor, lineTransparency

Set args = WScript.Arguments
If args.Count <> 2 Then
    WScript.Echo "Usage: cscript inspect_pptx.vbs <source.pptx> <output.tsv>"
    WScript.Quit 2
End If

sourcePath = args(0)
outputPath = args(1)
Set fso = CreateObject("Scripting.FileSystemObject")

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

Set outFile = fso.CreateTextFile(outputPath, True, True)
outFile.WriteLine "slide" & vbTab & "index" & vbTab & "name" & vbTab & "type" & vbTab & "autoshape" & vbTab & "left" & vbTab & "top" & vbTab & "width" & vbTab & "height" & vbTab & "rotation" & vbTab & "text" & vbTab & "font_name" & vbTab & "font_size" & vbTab & "font_bold" & vbTab & "font_color" & vbTab & "fill_color" & vbTab & "fill_transparency" & vbTab & "line_color" & vbTab & "line_transparency"

For Each slide In deck.Slides
    For Each shape In slide.Shapes
        Err.Clear
        textValue = ""
        fontName = ""
        fontSize = ""
        fontBold = ""
        fontColor = ""
        fillColor = ""
        fillTransparency = ""
        lineColor = ""
        lineTransparency = ""

        If shape.HasTextFrame Then
            If shape.TextFrame.HasText Then
                textValue = shape.TextFrame.TextRange.Text
                textValue = Replace(textValue, vbCr, "\n")
                textValue = Replace(textValue, vbLf, "\n")
                textValue = Replace(textValue, vbTab, " ")
                fontName = shape.TextFrame.TextRange.Font.Name
                fontSize = shape.TextFrame.TextRange.Font.Size
                fontBold = shape.TextFrame.TextRange.Font.Bold
                fontColor = shape.TextFrame.TextRange.Font.Color.RGB
            End If
        End If

        If shape.Fill.Visible Then
            fillColor = shape.Fill.ForeColor.RGB
            fillTransparency = shape.Fill.Transparency
        End If
        If shape.Line.Visible Then
            lineColor = shape.Line.ForeColor.RGB
            lineTransparency = shape.Line.Transparency
        End If

        outFile.WriteLine slide.SlideIndex & vbTab & shape.ZOrderPosition & vbTab & shape.Name & vbTab & shape.Type & vbTab & shape.AutoShapeType & vbTab & shape.Left & vbTab & shape.Top & vbTab & shape.Width & vbTab & shape.Height & vbTab & shape.Rotation & vbTab & textValue & vbTab & fontName & vbTab & fontSize & vbTab & fontBold & vbTab & fontColor & vbTab & fillColor & vbTab & fillTransparency & vbTab & lineColor & vbTab & lineTransparency
    Next
Next

outFile.Close
deck.Close
app.Quit
On Error GoTo 0

WScript.Echo outputPath
