import json
import sys
import re

from utility import Type

re_impl = r'^module\s+(.+)$'
re_export = r'^export\s+module\s+(.+)$'

re_export_import = r'^export\s+import\s+(.+)$'
re_import = r'^import\s+(.+)$'
re_import_header_unit = r'^[<"]+.+[>"]+$'

re_okay = [r'^module$', r'^#.+$', r'^//.*$']

def classify(filename):
    data = {'filename': filename, 'module': '', 'type': Type.plain, 'header_units': [], 'post': [], 'pre': []}

    done = False
    with open(filename) as file:
        for line in file:
            for stat in line.strip().split(';'):
                stat = stat.strip()
                if len(stat) > 0:
                    assigned = False

                    m_impl = re.search(re_impl, stat)
                    if m_impl is not None:
                        assert(data['type'] == Type.plain)
                        data['type'] = Type.module_impl
                        data['module'] = m_impl.groups()[0]
                        data['pre'] += [data['module']]
                        assigned = True

                    if not assigned:
                        m_export = re.search(re_export, stat)
                        if m_export is not None:
                            assert(data['type'] == Type.plain)
                            is_partition = ':' in stat
                            data['type'] = Type.module_partition if is_partition else Type.module
                            data['module'] = m_export.groups()[0]
                            assigned = True

                    if not assigned:
                        m_export_import = re.search(re_export_import, stat)
                        if m_export_import is not None:
                            value = m_export_import.groups()[0]
                            if value[0] == ':':
                                assert(data['module'] != '')
                                value = data['module'] + value
                            data['post'] += [value]
                            assigned = True

                    if not assigned:
                        m_import = re.search(re_import, stat)
                        if m_import is not None:
                            what = m_import.groups()[0]

                            m_import_header_unit = re.search(re_import_header_unit, what)
                            if m_import_header_unit is not None:
                                what = what.strip('<>"')
                                data['header_units'] += [what]
                            elif what[0] == ':':
                                assert(data['module'] != '')
                                what = data['module'].split(':')[0] + what
                                data['post'] += [what]
                            else:
                                data['post'] += [what]

                            assigned = True

                    if not assigned:
                        for regexp in re_okay:
                            if re.search(regexp, stat) is not None:
                                continue
                        done = True
                        break

            if done:
                break

    return data


if __name__ == '__main__':
    data = classify(sys.argv[1])
    print(data)
