from tools.package_source_candidate import ROOT_FILES, source_files


def test_original_npm_notices_are_not_confused_with_installed_dependencies(tmp_path):
    for relative in ROOT_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('source fixture')
    notice = tmp_path / 'licenses/npm/node_modules/react/LICENSE'
    notice.parent.mkdir(parents=True)
    notice.write_text('Original upstream copyright and license')
    dependency = tmp_path / 'frontend/node_modules/react/index.js'
    dependency.parent.mkdir(parents=True)
    dependency.write_text('development dependency')
    assert notice in source_files(tmp_path)
    assert dependency not in source_files(tmp_path)
