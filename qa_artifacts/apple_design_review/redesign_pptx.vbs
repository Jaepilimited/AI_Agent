Option Explicit

Const MSO_TRUE = -1
Const MSO_FALSE = 0
Const MSO_SHAPE_ROUNDED_RECTANGLE = 5
Const PPTX_FORMAT = 24

Dim FONT_KR, FONT_TITLE
Dim COLOR_BG, COLOR_SURFACE, COLOR_TEXT, COLOR_BODY, COLOR_SECONDARY
Dim COLOR_ACCENT, COLOR_ACCENT_SOFT, COLOR_SEPARATOR, COLOR_DARK
Dim OLD_ACCENT

FONT_KR = "Noto Sans KR"
FONT_TITLE = "Malgun Gothic"
COLOR_BG = RGB(245, 245, 247)
COLOR_SURFACE = RGB(255, 255, 255)
COLOR_TEXT = RGB(29, 29, 31)
COLOR_BODY = RGB(58, 58, 60)
COLOR_SECONDARY = RGB(110, 110, 115)
COLOR_ACCENT = RGB(255, 149, 0)
COLOR_ACCENT_SOFT = RGB(255, 246, 232)
COLOR_SEPARATOR = RGB(210, 210, 215)
COLOR_DARK = RGB(29, 29, 31)
OLD_ACCENT = RGB(232, 146, 0)

Dim args, sourcePath, outputPath, copyDir, fso, app, deck, slide
Dim titles, slideNumber

Set args = WScript.Arguments
If args.Count <> 3 Then
    WScript.Echo "Usage: cscript redesign_pptx.vbs <source.pptx> <output.pptx> <copy-dir>"
    WScript.Quit 2
End If

sourcePath = args(0)
outputPath = args(1)
copyDir = args(2)
Set fso = CreateObject("Scripting.FileSystemObject")

If Not fso.FileExists(sourcePath) Then
    Err.Raise vbObjectError + 100, , "PowerPoint file not found: " & sourcePath
End If
If fso.FileExists(outputPath) Then
    Err.Raise vbObjectError + 101, , "Output already exists: " & outputPath
End If
EnsureFolder fso.GetParentFolderName(outputPath)

Set titles = LoadTitleMap(fso.BuildPath(copyDir, "titles.tsv"))

On Error Resume Next
Set app = CreateObject("PowerPoint.Application")
If Err.Number <> 0 Then
    WScript.Echo "CREATE_ERROR " & Err.Number & " " & Err.Description
    WScript.Quit 3
End If

Set deck = app.Presentations.Open(sourcePath, MSO_TRUE, MSO_TRUE, MSO_FALSE)
If Err.Number <> 0 Then
    WScript.Echo "OPEN_ERROR " & Err.Number & " " & Err.Description
    app.Quit
    WScript.Quit 4
End If
On Error GoTo 0

For Each slide In deck.Slides
    slideNumber = slide.SlideIndex
    If slideNumber = 1 Then
        StyleCover slide, fso.BuildPath(copyDir, "cover-body.txt")
    ElseIf slideNumber = 24 Then
        StyleClosing slide
    Else
        Select Case slideNumber
            Case 2
                StyleTextSlide slide, 16.5, 19
            Case 3
                StyleOriginSlide slide
            Case 4
                StyleComparisonSlide slide
            Case 5
                StyleProcessSlide slide
            Case 6, 7, 8, 9, 10, 12, 16, 17, 19, 21
                StyleScreenshotSlide slide, 14.2, 16.5
            Case 11
                StyleGuideCharacterSlide slide
            Case 13, 15, 22, 23
                StyleTextSlide slide, 16.8, 19
            Case 14
                slide.Shapes(4).TextFrame.TextRange.Text = ReadUtf8Text(fso.BuildPath(copyDir, "slide-14-body.txt"))
                StyleTextSlide slide, 14, 17
            Case 18
                slide.Shapes(5).TextFrame.TextRange.Text = ReadUtf8Text(fso.BuildPath(copyDir, "slide-18-body.txt"))
                StyleScreenshotSlide slide, 13.1, 15.6
            Case 20
                slide.Shapes(5).TextFrame.TextRange.Text = ReadUtf8Text(fso.BuildPath(copyDir, "slide-20-body.txt"))
                StyleScreenshotSlide slide, 13.1, 15.6
        End Select

        StyleBaseSlide slide, titles
    End If
Next

On Error Resume Next
deck.SaveCopyAs outputPath, PPTX_FORMAT, MSO_FALSE
If Err.Number <> 0 Then
    Dim saveError
    saveError = Err.Description
    Err.Clear
    deck.Saved = MSO_TRUE
    deck.Close
    app.Quit
    WScript.Echo "SAVE_ERROR " & saveError
    WScript.Quit 5
End If

deck.Saved = MSO_TRUE
deck.Close
app.Quit
On Error GoTo 0

WScript.Echo outputPath

Sub EnsureFolder(ByVal folderPath)
    Dim parentPath
    If Len(folderPath) = 0 Then Exit Sub
    If fso.FolderExists(folderPath) Then Exit Sub
    parentPath = fso.GetParentFolderName(folderPath)
    If Len(parentPath) > 0 And Not fso.FolderExists(parentPath) Then EnsureFolder parentPath
    fso.CreateFolder folderPath
End Sub

Function ReadUtf8Text(ByVal filePath)
    Dim stream, value
    Set stream = CreateObject("ADODB.Stream")
    stream.Type = 2
    stream.Charset = "utf-8"
    stream.Open
    stream.LoadFromFile filePath
    value = stream.ReadText
    stream.Close
    value = Replace(value, vbCrLf, vbCr)
    value = Replace(value, vbLf, vbCr)
    Do While Len(value) > 0 And (Right(value, 1) = vbCr Or Right(value, 1) = vbLf)
        value = Left(value, Len(value) - 1)
    Loop
    ReadUtf8Text = value
End Function

Function LoadTitleMap(ByVal filePath)
    Dim map, raw, lines, line, parts, i
    Set map = CreateObject("Scripting.Dictionary")
    raw = ReadUtf8Text(filePath)
    lines = Split(raw, vbCr)
    For i = 0 To UBound(lines)
        line = Trim(lines(i))
        If Len(line) > 0 Then
            parts = Split(line, vbTab, 2)
            If UBound(parts) = 1 Then map(CStr(parts(0))) = parts(1)
        End If
    Next
    Set LoadTitleMap = map
End Function

Sub SetBounds(ByVal shape, ByVal leftValue, ByVal topValue, ByVal widthValue, ByVal heightValue)
    On Error Resume Next
    shape.Left = leftValue
    shape.Top = topValue
    shape.Width = widthValue
    shape.Height = heightValue
    On Error GoTo 0
End Sub

Sub SetSlideBackground(ByVal targetSlide, ByVal colorValue)
    On Error Resume Next
    targetSlide.FollowMasterBackground = MSO_FALSE
    targetSlide.Background.Fill.Solid
    targetSlide.Background.Fill.ForeColor.RGB = colorValue
    On Error GoTo 0
End Sub

Sub RemoveShadow(ByVal shape)
    On Error Resume Next
    shape.Shadow.Visible = MSO_FALSE
    On Error GoTo 0
End Sub

Sub ApplySoftShadow(ByVal shape, ByVal blurValue, ByVal transparencyValue, ByVal offsetYValue)
    On Error Resume Next
    shape.Shadow.Visible = MSO_TRUE
    shape.Shadow.ForeColor.RGB = RGB(0, 0, 0)
    shape.Shadow.Transparency = transparencyValue
    shape.Shadow.Blur = blurValue
    shape.Shadow.OffsetX = 0
    shape.Shadow.OffsetY = offsetYValue
    On Error GoTo 0
End Sub

Sub StyleHeaderSurface(ByVal shape)
    On Error Resume Next
    SetBounds shape, 0, 0, 960, 64
    shape.Fill.Visible = MSO_TRUE
    shape.Fill.Solid
    shape.Fill.ForeColor.RGB = COLOR_SURFACE
    shape.Fill.Transparency = 0.03
    shape.Line.Visible = MSO_FALSE
    ApplySoftShadow shape, 6, 0.9, 1.5
    On Error GoTo 0
End Sub

Sub StyleTitle(ByVal shape, ByVal titleText)
    On Error Resume Next
    If Len(titleText) > 0 Then shape.TextFrame.TextRange.Text = titleText
    shape.TextFrame.AutoSize = 0
    shape.TextFrame.WordWrap = MSO_FALSE
    shape.TextFrame2.AutoSize = 0
    shape.TextFrame2.WordWrap = MSO_FALSE
    shape.Fill.Visible = MSO_FALSE
    shape.Line.Visible = MSO_FALSE
    shape.TextFrame.MarginLeft = 0
    shape.TextFrame.MarginRight = 0
    shape.TextFrame.MarginTop = 0
    shape.TextFrame.MarginBottom = 0
    shape.TextFrame.WordWrap = MSO_FALSE
    shape.TextFrame.TextRange.Font.Name = FONT_TITLE
    shape.TextFrame.TextRange.Font.NameFarEast = FONT_TITLE
    shape.TextFrame.TextRange.Font.Size = 22.5
    shape.TextFrame.TextRange.Font.Bold = MSO_TRUE
    shape.TextFrame.TextRange.Font.Color.RGB = COLOR_TEXT
    SetBounds shape, 44, 13, 800, 40
    On Error GoTo 0
End Sub

Sub StyleWordmark(ByVal shape)
    On Error Resume Next
    shape.Visible = MSO_TRUE
    shape.TextFrame.TextRange.Text = "Cella"
    shape.TextFrame.AutoSize = 0
    shape.TextFrame.WordWrap = MSO_FALSE
    shape.TextFrame2.AutoSize = 0
    shape.TextFrame2.WordWrap = MSO_FALSE
    SetBounds shape, 868, 16, 72, 30
    shape.Fill.Visible = MSO_FALSE
    shape.Line.Visible = MSO_FALSE
    shape.TextFrame.MarginLeft = 0
    shape.TextFrame.MarginRight = 0
    shape.TextFrame.MarginTop = 0
    shape.TextFrame.MarginBottom = 0
    shape.TextFrame.TextRange.Font.Name = "Arial"
    shape.TextFrame.TextRange.Font.Size = 15.5
    shape.TextFrame.TextRange.Font.Bold = MSO_TRUE
    shape.TextFrame.TextRange.Font.Color.RGB = COLOR_ACCENT
    shape.TextFrame.TextRange.ParagraphFormat.Alignment = 3
    On Error GoTo 0
End Sub

Sub StyleBaseSlide(ByVal targetSlide, ByVal titleMap)
    Dim titleValue, titleShape, wordmarkShape
    SetSlideBackground targetSlide, COLOR_BG
    StyleHeaderSurface targetSlide.Shapes(1)

    titleValue = ""
    If titleMap.Exists(CStr(targetSlide.SlideIndex)) Then titleValue = titleMap(CStr(targetSlide.SlideIndex))

    On Error Resume Next
    targetSlide.Shapes(3).Delete
    targetSlide.Shapes(2).Delete
    Set titleShape = targetSlide.Shapes.AddTextbox(1, 44, 13, 800, 40)
    Set wordmarkShape = targetSlide.Shapes.AddTextbox(1, 868, 16, 72, 30)
    On Error GoTo 0

    StyleTitle titleShape, titleValue
    StyleWordmark wordmarkShape
End Sub

Sub StyleCover(ByVal targetSlide, ByVal coverBodyPath)
    Dim backgroundShape, pictureShape, textShape, textRange, coverBodyText
    SetSlideBackground targetSlide, COLOR_BG
    Set backgroundShape = targetSlide.Shapes(1)
    Set pictureShape = targetSlide.Shapes(2)
    Set textShape = targetSlide.Shapes(3)

    On Error Resume Next
    SetBounds backgroundShape, 0, 0, 960, 540
    backgroundShape.Fill.Solid
    backgroundShape.Fill.ForeColor.RGB = COLOR_BG
    backgroundShape.Line.Visible = MSO_FALSE
    RemoveShadow backgroundShape

    pictureShape.LockAspectRatio = MSO_TRUE
    pictureShape.Height = 360
    pictureShape.Left = 704
    pictureShape.Top = 88
    pictureShape.Line.Visible = MSO_FALSE
    ApplySoftShadow pictureShape, 16, 0.84, 6

    coverBodyText = ReadUtf8Text(coverBodyPath)
    textShape.TextFrame.TextRange.Text = coverBodyText
    textShape.TextFrame.AutoSize = 0
    textShape.TextFrame.WordWrap = MSO_TRUE
    textShape.TextFrame2.AutoSize = 0
    SetBounds textShape, 76, 142, 575, 286
    textShape.Fill.Visible = MSO_FALSE
    textShape.Line.Visible = MSO_FALSE
    textShape.TextFrame.MarginLeft = 0
    textShape.TextFrame.MarginRight = 0
    textShape.TextFrame.MarginTop = 0
    textShape.TextFrame.MarginBottom = 0
    textShape.TextFrame.TextRange.Font.Name = FONT_KR
    Set textRange = textShape.TextFrame.TextRange

    StyleCoverParagraph textRange.Paragraphs(1), 48, MSO_TRUE, COLOR_TEXT, 8
    StyleCoverParagraph textRange.Paragraphs(2), 21, MSO_FALSE, COLOR_SECONDARY, 2
    StyleCoverParagraph textRange.Paragraphs(3), 21, MSO_FALSE, COLOR_SECONDARY, 12
    StyleCoverParagraph textRange.Paragraphs(4), 19, MSO_TRUE, COLOR_ACCENT, 18
    StyleCoverParagraph textRange.Paragraphs(5), 12.5, MSO_FALSE, COLOR_SECONDARY, 0
    On Error GoTo 0
End Sub

Sub StyleCoverParagraph(ByVal paragraphRange, ByVal fontSizeValue, ByVal boldValue, ByVal colorValue, ByVal spacingAfter)
    On Error Resume Next
    paragraphRange.Font.Name = FONT_KR
    paragraphRange.Font.Size = fontSizeValue
    paragraphRange.Font.Bold = boldValue
    paragraphRange.Font.Color.RGB = colorValue
    paragraphRange.ParagraphFormat.SpaceAfter = spacingAfter
    On Error GoTo 0
End Sub

Sub StyleClosing(ByVal targetSlide)
    Dim backgroundShape, textShape, textRange
    SetSlideBackground targetSlide, COLOR_DARK
    Set backgroundShape = targetSlide.Shapes(1)
    Set textShape = targetSlide.Shapes(2)

    On Error Resume Next
    SetBounds backgroundShape, 0, 0, 960, 540
    backgroundShape.Fill.Solid
    backgroundShape.Fill.ForeColor.RGB = COLOR_DARK
    backgroundShape.Line.Visible = MSO_FALSE
    RemoveShadow backgroundShape

    SetBounds textShape, 92, 160, 780, 250
    textShape.Fill.Visible = MSO_FALSE
    textShape.Line.Visible = MSO_FALSE
    textShape.TextFrame.MarginLeft = 0
    textShape.TextFrame.MarginRight = 0
    textShape.TextFrame.MarginTop = 0
    textShape.TextFrame.MarginBottom = 0
    textShape.TextFrame.TextRange.Font.Name = FONT_KR
    Set textRange = textShape.TextFrame.TextRange
    StyleCoverParagraph textRange.Paragraphs(1), 40, MSO_TRUE, RGB(255, 255, 255), 20
    StyleCoverParagraph textRange.Paragraphs(2), 25, MSO_TRUE, COLOR_ACCENT, 20
    StyleCoverParagraph textRange.Paragraphs(3), 17, MSO_FALSE, RGB(245, 245, 247), 30
    StyleCoverParagraph textRange.Paragraphs(4), 12.5, MSO_FALSE, RGB(142, 142, 147), 0
    On Error GoTo 0
End Sub

Sub StyleOriginSlide(ByVal targetSlide)
    Dim pictureShape, textShape
    Set pictureShape = targetSlide.Shapes(4)
    Set textShape = targetSlide.Shapes(5)

    On Error Resume Next
    pictureShape.LockAspectRatio = MSO_TRUE
    pictureShape.Height = 400
    pictureShape.Left = 96
    pictureShape.Top = 92
    pictureShape.Line.Visible = MSO_FALSE
    ApplySoftShadow pictureShape, 14, 0.86, 5
    On Error GoTo 0

    SetBounds textShape, 410, 92, 500, 410
    StyleFlatTextBox textShape, 16.2, 18.5, targetSlide.SlideIndex
End Sub

Sub StyleTextSlide(ByVal targetSlide, ByVal bodySize, ByVal headingSize)
    Dim textShape
    Set textShape = targetSlide.Shapes(4)
    SetBounds textShape, 62, 88, 836, 422
    StyleFlatTextBox textShape, bodySize, headingSize, targetSlide.SlideIndex
End Sub

Sub StyleScreenshotSlide(ByVal targetSlide, ByVal bodySize, ByVal headingSize)
    Dim pictureShape, textShape
    Set pictureShape = targetSlide.Shapes(4)
    Set textShape = targetSlide.Shapes(5)

    On Error Resume Next
    pictureShape.LockAspectRatio = MSO_TRUE
    pictureShape.Width = 610
    pictureShape.Left = 28
    pictureShape.Top = 88
    pictureShape.Line.Visible = MSO_TRUE
    pictureShape.Line.ForeColor.RGB = COLOR_SEPARATOR
    pictureShape.Line.Weight = 0.6
    ApplySoftShadow pictureShape, 12, 0.84, 5
    On Error GoTo 0

    SetBounds textShape, 656, 82, 286, 432
    StylePanelTextBox textShape, bodySize, headingSize, targetSlide.SlideIndex
End Sub

Sub StyleGuideCharacterSlide(ByVal targetSlide)
    Dim pictureShape, textShape
    Set pictureShape = targetSlide.Shapes(4)
    Set textShape = targetSlide.Shapes(5)

    On Error Resume Next
    pictureShape.LockAspectRatio = MSO_TRUE
    pictureShape.Height = 420
    pictureShape.Left = 86
    pictureShape.Top = 84
    pictureShape.Line.Visible = MSO_FALSE
    ApplySoftShadow pictureShape, 14, 0.86, 5
    On Error GoTo 0

    SetBounds textShape, 360, 82, 582, 432
    StylePanelTextBox textShape, 14.8, 17, targetSlide.SlideIndex
End Sub

Sub StylePanelTextBox(ByVal shape, ByVal bodySize, ByVal headingSize, ByVal targetSlideNumber)
    On Error Resume Next
    shape.AutoShapeType = MSO_SHAPE_ROUNDED_RECTANGLE
    shape.Adjustments.Item(1) = 0.08
    shape.Fill.Visible = MSO_TRUE
    shape.Fill.Solid
    shape.Fill.ForeColor.RGB = COLOR_SURFACE
    shape.Fill.Transparency = 0
    shape.Line.Visible = MSO_TRUE
    shape.Line.ForeColor.RGB = COLOR_SEPARATOR
    shape.Line.Transparency = 0.35
    shape.Line.Weight = 0.75
    ApplySoftShadow shape, 12, 0.88, 4
    shape.TextFrame.MarginLeft = 16
    shape.TextFrame.MarginRight = 16
    shape.TextFrame.MarginTop = 14
    shape.TextFrame.MarginBottom = 12
    shape.TextFrame.VerticalAnchor = 1
    shape.TextFrame.WordWrap = MSO_TRUE
    shape.TextFrame.AutoSize = 0
    On Error GoTo 0
    StyleParagraphs shape, bodySize, headingSize, targetSlideNumber
End Sub

Sub StyleFlatTextBox(ByVal shape, ByVal bodySize, ByVal headingSize, ByVal targetSlideNumber)
    On Error Resume Next
    shape.Fill.Visible = MSO_FALSE
    shape.Line.Visible = MSO_FALSE
    RemoveShadow shape
    shape.TextFrame.MarginLeft = 4
    shape.TextFrame.MarginRight = 4
    shape.TextFrame.MarginTop = 4
    shape.TextFrame.MarginBottom = 4
    shape.TextFrame.VerticalAnchor = 1
    shape.TextFrame.WordWrap = MSO_TRUE
    shape.TextFrame.AutoSize = 0
    On Error GoTo 0
    StyleParagraphs shape, bodySize, headingSize, targetSlideNumber
End Sub

Function IsReplacementHeading(ByVal targetSlideNumber, ByVal paragraphIndex)
    IsReplacementHeading = False
    If targetSlideNumber = 14 Then
        If paragraphIndex = 1 Or paragraphIndex = 4 Or paragraphIndex = 7 Or paragraphIndex = 11 Then IsReplacementHeading = True
    ElseIf targetSlideNumber = 18 Then
        If paragraphIndex = 2 Or paragraphIndex = 6 Or paragraphIndex = 9 Then IsReplacementHeading = True
    ElseIf targetSlideNumber = 20 Then
        If paragraphIndex = 2 Or paragraphIndex = 5 Or paragraphIndex = 7 Then IsReplacementHeading = True
    End If
End Function

Sub StyleParagraphs(ByVal shape, ByVal bodySize, ByVal headingSize, ByVal targetSlideNumber)
    Dim textRange, paragraphRange, i, originalColor, isHeading, rawText
    On Error Resume Next
    Set textRange = shape.TextFrame.TextRange
    textRange.Font.Name = FONT_KR

    For i = 1 To textRange.Paragraphs.Count
        Set paragraphRange = textRange.Paragraphs(i)
        rawText = Trim(Replace(Replace(paragraphRange.Text, vbCr, ""), vbLf, ""))
        originalColor = paragraphRange.Font.Color.RGB
        If targetSlideNumber = 14 Or targetSlideNumber = 18 Or targetSlideNumber = 20 Then
            isHeading = IsReplacementHeading(targetSlideNumber, i)
        Else
            isHeading = (originalColor = OLD_ACCENT Or originalColor = COLOR_ACCENT)
        End If

        paragraphRange.Font.Name = FONT_KR
        paragraphRange.Font.Size = bodySize
        paragraphRange.Font.Color.RGB = COLOR_BODY
        paragraphRange.ParagraphFormat.SpaceAfter = 3

        If isHeading Then
            paragraphRange.Font.Size = headingSize
            paragraphRange.Font.Bold = MSO_TRUE
            paragraphRange.Font.Color.RGB = COLOR_ACCENT
            paragraphRange.ParagraphFormat.SpaceBefore = 7
            paragraphRange.ParagraphFormat.SpaceAfter = 2
        ElseIf Len(rawText) > 0 And AscW(Left(rawText, 1)) = 8251 Then
            paragraphRange.Font.Size = bodySize - 1.6
            paragraphRange.Font.Color.RGB = COLOR_SECONDARY
            paragraphRange.ParagraphFormat.SpaceBefore = 3
        End If
    Next
    On Error GoTo 0
End Sub

Sub StyleComparisonSlide(ByVal targetSlide)
    Dim tableShape, summaryShape, tableObject, rowIndex, columnIndex, cellShape, borderIndex
    Set tableShape = targetSlide.Shapes(4)
    Set summaryShape = targetSlide.Shapes(5)

    SetBounds tableShape, 52, 88, 856, 368
    On Error Resume Next
    Set tableObject = tableShape.Table
    tableObject.Columns(1).Width = 236
    tableObject.Columns(2).Width = 332
    tableObject.Columns(3).Width = 288

    For rowIndex = 1 To tableObject.Rows.Count
        For columnIndex = 1 To tableObject.Columns.Count
            Set cellShape = tableObject.Cell(rowIndex, columnIndex).Shape
            cellShape.TextFrame.MarginLeft = 8
            cellShape.TextFrame.MarginRight = 8
            cellShape.TextFrame.MarginTop = 6
            cellShape.TextFrame.MarginBottom = 4
            cellShape.TextFrame.TextRange.Font.Name = FONT_KR
            cellShape.TextFrame.TextRange.Font.Size = 12.8
            cellShape.TextFrame.TextRange.Font.Color.RGB = COLOR_BODY

            If rowIndex = 1 Then
                cellShape.Fill.ForeColor.RGB = RGB(242, 242, 247)
                cellShape.TextFrame.TextRange.Font.Bold = MSO_TRUE
                cellShape.TextFrame.TextRange.Font.Size = 14
                cellShape.TextFrame.TextRange.Font.Color.RGB = COLOR_TEXT
            ElseIf columnIndex = 2 Then
                cellShape.Fill.ForeColor.RGB = COLOR_ACCENT_SOFT
            ElseIf (rowIndex Mod 2) = 0 Then
                cellShape.Fill.ForeColor.RGB = COLOR_SURFACE
            Else
                cellShape.Fill.ForeColor.RGB = RGB(249, 249, 251)
            End If

            If columnIndex = 1 And rowIndex > 1 Then cellShape.TextFrame.TextRange.Font.Bold = MSO_TRUE

            For borderIndex = 1 To 4
                tableObject.Cell(rowIndex, columnIndex).Borders(borderIndex).ForeColor.RGB = COLOR_SEPARATOR
                tableObject.Cell(rowIndex, columnIndex).Borders(borderIndex).Weight = 0.5
            Next
        Next
    Next
    tableShape.Line.Visible = MSO_FALSE
    RemoveShadow tableShape
    On Error GoTo 0

    SetBounds summaryShape, 60, 469, 840, 40
    On Error Resume Next
    summaryShape.Fill.Visible = MSO_FALSE
    summaryShape.Line.Visible = MSO_FALSE
    summaryShape.TextFrame.MarginLeft = 0
    summaryShape.TextFrame.MarginRight = 0
    summaryShape.TextFrame.MarginTop = 0
    summaryShape.TextFrame.MarginBottom = 0
    summaryShape.TextFrame.TextRange.Font.Name = FONT_KR
    summaryShape.TextFrame.TextRange.Font.Size = 11.5
    summaryShape.TextFrame.TextRange.Font.Color.RGB = COLOR_SECONDARY
    On Error GoTo 0
End Sub

Sub StyleProcessSlide(ByVal targetSlide)
    Dim cardIndexes, arrowIndexes, cardPositions, i, shape, textRange
    cardIndexes = Array(4, 6, 8, 10, 12)
    arrowIndexes = Array(5, 7, 9, 11)
    cardPositions = Array(36, 225, 414, 603, 792)

    For i = 0 To UBound(cardIndexes)
        Set shape = targetSlide.Shapes(cardIndexes(i))
        SetBounds shape, cardPositions(i), 134, 156, 186
        On Error Resume Next
        shape.AutoShapeType = MSO_SHAPE_ROUNDED_RECTANGLE
        shape.Adjustments.Item(1) = 0.1
        shape.Fill.Visible = MSO_TRUE
        shape.Fill.Solid
        shape.Fill.ForeColor.RGB = COLOR_SURFACE
        shape.Line.Visible = MSO_TRUE
        shape.Line.ForeColor.RGB = COLOR_SEPARATOR
        shape.Line.Transparency = 0.25
        shape.Line.Weight = 0.75
        ApplySoftShadow shape, 10, 0.86, 4
        shape.TextFrame.MarginLeft = 12
        shape.TextFrame.MarginRight = 12
        shape.TextFrame.MarginTop = 14
        shape.TextFrame.MarginBottom = 10
        shape.TextFrame.WordWrap = MSO_TRUE
        Set textRange = shape.TextFrame.TextRange
        textRange.Font.Name = FONT_KR
        textRange.Font.Size = 12.3
        textRange.Font.Color.RGB = COLOR_SECONDARY
        textRange.Paragraphs(1).Font.Size = 14.2
        textRange.Paragraphs(1).Font.Bold = MSO_TRUE
        textRange.Paragraphs(1).Font.Color.RGB = COLOR_TEXT
        On Error GoTo 0
    Next

    For i = 0 To UBound(arrowIndexes)
        Set shape = targetSlide.Shapes(arrowIndexes(i))
        SetBounds shape, 192 + (189 * i), 207, 30, 32
        On Error Resume Next
        shape.Fill.Visible = MSO_TRUE
        shape.Fill.Solid
        shape.Fill.ForeColor.RGB = COLOR_ACCENT
        shape.Fill.Transparency = 0.08
        shape.Line.Visible = MSO_FALSE
        RemoveShadow shape
        On Error GoTo 0
    Next

    Set shape = targetSlide.Shapes(13)
    SetBounds shape, 62, 360, 836, 132
    StyleFlatTextBox shape, 15.2, 18, targetSlide.SlideIndex
End Sub
