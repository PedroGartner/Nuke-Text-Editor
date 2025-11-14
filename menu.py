import TextEditor
scripts_m = nuke.menu('Nuke').addMenu('PGartner')
# call the show_texteditor function (not a class)
scripts_m.addCommand('Text Editor', 'TextEditor.show_texteditor()')
	