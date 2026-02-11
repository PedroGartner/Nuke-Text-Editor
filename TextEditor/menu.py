import TextEditor
scripts_m = nuke.menu('Nuke').addMenu('Tools')
scripts_m.addCommand('Text Editor', 'TextEditor.show_texteditor()')
