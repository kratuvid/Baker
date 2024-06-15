#!/usr/bin/env python3

import json
import os
import re
import resource
import subprocess
import sys
import time

from node import Node
from utility import Type
from classify import classify

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

class Baker:
    args_nature = {
        'help': 0,
        'release': 0,
        'run': 1,
        'show': 0,
        'rebuild': 0,
        'default_bakerfile': 0,
        'dump_bakerfile': 0,
        'deptree': 0,
        'tree': 0,
        'maxrss': 0
    }

    options_default = {
        'dirs': {
            'source': 'src',
            'build': 'build',
            'object': 'object',
            'header_units': 'header_units',
        },
        'flags': {
            'debug': ['-g', '-DDEBUG'],
            'release': ['-O3', '-DNDEBUG'],
            'base': ['-std=c++23', '-Wno-experimental-header-units']
        },
        'options': {
            'cxx': 'clang++'
        },
    }

    def __init__(self):
        self.load_bakerfile()
        self.handle_args()
        self.make_targets()

        if 'maxrss' in self.args:
            # in KBs on Linux, https://man7.org/linux/man-pages/man2/getrusage.2.html
            maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            maxrss_children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
            eprint('> Peak RSS usage: '
                   + f'Self: {maxrss} KiB ({maxrss/1024} MiB), '
                   + f'Children: {maxrss_children} KiB ({maxrss_children/1024} MiB)')

    def make_targets(self):
        targets = self.options['targets']
        if type(targets) != dict:
            raise ValueError(f'Targets must be a dict: {targets}')

        for target in targets:
            sources = targets[target]
            if type(sources) != list:
                raise ValueError(f'Source of each target must be a list (of strings): {targets}')

            begin = time.time()
            self.gen_classes(sources)
            self.make_directories()
            self.make_header_units()
            self.build_dependency_tree()
            if 'deptree' in self.args:
                print(f'{target}:')
                self.walk(self.root_node, 0)
            else:
                self.build_compile_tree()
                if 'tree' in self.args:
                    print(f'{target}:')
                    self.walk(self.root_node, 0)
                else:
                    self.compile_all()
                    self.link(target)
            elapsed = time.time() - begin
            eprint('> Completed', target, 'in', f'{elapsed:.2e}s')

        if 'run' in self.args:
            target = self.args['run'][0]
            if target not in targets:
                raise ValueError(f'Can\'t run invalid target {target}')
            target_path = os.path.join(self.dirs['build'], target)
            eprint(f'> Run {target}')
            self.run([target_path])

    def compile_all(self):
        self.compiles = 0
        triggered_recompile = self.rebuild
        triggered_recompile_next = False

        node = self.last_node
        siblings_left = node.parent.children.copy() if node.parent is not None else None

        while True:
            raw_source = node.data['filename']

            source = os.path.join(self.options['dirs']['source'], raw_source)
            target = os.path.join(self.dirs['object'], self.removesuffixes(['.cpp', '.cppm'], raw_source) + '.o')

            is_module = node.data['type'] in [Type.module, Type.module_partition]
            bmi_target = target.removesuffix('.o') + '.pcm'

            if triggered_recompile or (not os.path.exists(target) or self.is_later(source, target)) \
               or (is_module and (not os.path.exists(bmi_target) or self.is_later(source, bmi_target))):

                if not self.show:
                    eprint(f'> Compiling {raw_source}...', end='', flush=True)
                begin = time.time()
                self.compile(source, target, node)
                elapsed = time.time() - begin
                if not self.show:
                    eprint('\r', end='')
                eprint('> Compiled', raw_source, 'in', f'{elapsed:.2e}s')

                self.compiles += 1
                triggered_recompile_next = True

            if id(node) == id(self.root_node):
                break

            siblings_left.remove(node)
            if len(siblings_left) > 0:
                node = siblings_left[0]
            else:
                if triggered_recompile_next:
                    triggered_recompile = True
                node = node.parent
                siblings_left = node.parent.children.copy() if node.parent is not None else None

    def compile(self, source, target, node):
        extra_flags = []

        for header in node.data['header_units']:
            bmi_path = os.path.join(self.dirs['header_units'], header.replace('/', '-')) + '.pcm'
            extra_flags += ['-fmodule-file=' + bmi_path]

        collected = []
        self.collect_modules(node, collected)
        for s_module, s_bmi_path in collected:
            extra_flags += ['-fmodule-file=' + s_module + '=' + s_bmi_path]

        self.run(self.cxx + self.base_flags + self.type_flags + extra_flags + ['-fmodule-output', '-c', source, '-o', target])

    def collect_modules(self, node, collected):
        if node.data['type'] in [Type.module, Type.module_partition]:
            filename = node.data['filename']
            bmi_path = os.path.join(self.dirs['object'], filename.removesuffix('.cppm') + '.pcm')
            collected += [(node.data['module'], bmi_path)]

        for child in node.children:
            self.collect_modules(child, collected)

    def link(self, target):
        if self.compiles > 0:
            collected = []
            self.collect_objects(self.root_node, collected)
            target_path = os.path.join(self.dirs['build'], target)

            if not self.show:
                eprint(f'> Linking {target}...', end='', flush=True)
            begin = time.time()
            self.run(self.cxx + self.base_flags + self.type_flags + collected + ['-o', target_path])
            elapsed = time.time() - begin
            if not self.show:
                eprint('\r', end='')
            eprint('> Linked', target, 'in', f'{elapsed:.2e}s ')

    def collect_objects(self, node, collected):
        collected += [self.removesuffixes(['.cpp', '.cppm'],
                                          os.path.join(self.dirs['object'], node.data['filename'])) + '.o']
        for child in node.children:
            self.collect_objects(child, collected)

    def removesuffixes(self, suffixes, path):
        for suffix in suffixes:
            path = path.removesuffix(suffix)
        return path

    def is_later(self, path, path2):
        return os.path.getmtime(path) > os.path.getmtime(path2)

    def make_header_units(self):
        for header in self.header_units:
            bmi_path = os.path.join(self.dirs['header_units'], header.replace('/', '-')) + '.pcm'
            if not os.path.exists(bmi_path):
                if not self.show:
                    eprint(f'> Precompiling header {header}...', end='', flush=True)
                begin = time.time()
                self.run(self.cxx + self.base_flags +
                         ['-Wno-pragma-system-header-outside-header', '--precompile', '-xc++-system-header',
                          header, '-o', bmi_path])
                if not self.show:
                    eprint('\r', end='')
                elapsed = time.time() - begin
                eprint('> Precompiled header', header, 'in', f'{elapsed:.2e}s')

    def build_compile_tree(self):
        self.clip_redundant(self.root_node)
        self.fix_module_partition_deps(self.root_node)

    def fix_module_partition_deps(self, node):
        for child in node.children:
            if node.data['type'] == Type.module and child.data['type'] == Type.module_partition:
                c_node = self.classes[Type.module][node.data['module']]
                c_partition_node = self.classes[Type.module_partition][child.data['module']]
                c_node.data['post'] += c_partition_node.data['post']

            self.fix_module_partition_deps(child)

    def clip_redundant(self, node):
        while True:
            any = False
            for index, child in enumerate(node.children):
                if id(child.parent) != id(node):
                    del node.children[index]
                    any = True
                    break
            if not any:
                break

        for child in node.children:
            self.clip_redundant(child)

    def walk(self, node, depth):
        type_name = node.data['type'].name
        id_str = f'0x{id(node):x}'
        parent_id_str = f'0x{id(node.parent):x}' if node.parent is not None else 'None'
        print(f'Depth: {depth}, id: {id_str}, parent id: {parent_id_str}, type: {type_name}'
              + (f' ({node.data["module"]})' if type_name.startswith('module') else '')
              + f', source: {node.data["filename"]}')
        for child in node.children:
            self.walk(child, depth+1)

    def build_dependency_tree(self):
        self.attach_plain_sources()
        self.attach_module_impls()
        self.node_depth = {}
        self.last_node_depth = -1
        self.fill_children(self.root_node, 0)

    def attach_plain_sources(self):
        for index, plain in enumerate(self.classes[Type.plain]):
            if id(plain) != id(self.root_node):
                self.root_node.children += ['@' + str(index)]

    def attach_module_impls(self):
        for key in self.classes[Type.module_impl]:
            for index in range(len(self.classes[Type.module_impl][key])):
                self.root_node.children += ['%' + key + ',' + str(index)]

    def fill_children(self, node, depth):
        if depth > self.last_node_depth:
            self.last_node_depth = depth
            self.last_node = node
        module = node.data['module']

        for index, child in enumerate(node.children):
            if type(child) == Node:
                continue
            if type(child) != str:
                raise RuntimeError(f'Non-string child {child} of node: {node}')

            if child in self.classes[Type.module]:
                node.children[index] = self.classes[Type.module][child]

            elif child in self.classes[Type.module_partition]:
                if module.split(':')[0] != child.split(':')[0]:
                    raise RuntimeError(f'Module {module} can\'t import a foreign parition {child}')
                node.children[index] = self.classes[Type.module_partition][child]

            elif child[0] == '%':
                module_impl, index2 = child[1:].split(',')
                node.children[index] = self.classes[Type.module_impl][module_impl][int(index2)]

            elif child[0] == '@':
                node.children[index] = self.classes[Type.plain][int(child[1:])]

            else:
                raise RuntimeError(f'No module named {module} is known. Did you forget to include its source?')

        for index in range(len(node.children)):
            child = node.children[index]
            if (child not in self.node_depth) or (depth > self.node_depth[child]):
                self.node_depth[child] = depth
                node.children[index].parent = node
            self.fill_children(node.children[index], depth+1)

    def make_directories(self):
        os.makedirs(self.dirs['build'], exist_ok=True)
        os.makedirs(self.dirs['object'], exist_ok=True)
        for dir in self.primary_dirs:
            os.makedirs(os.path.join(self.dirs['object'], dir), exist_ok=True)
        os.makedirs(self.dirs['header_units'], exist_ok=True)

    def gen_classes(self, sources):
        os.chdir(self.options['dirs']['source'])

        self.classes = {
            Type.plain: set(),
            Type.module: {},
            Type.module_partition: {},
            Type.module_impl: {}
        }
        self.header_units = set()
        self.primary_dirs = set()

        for index, source in enumerate(sources):
            if not (source.endswith('.cpp') or source.endswith('.cppm')):
                raise ValueError('Only .cpp and .cppm files are permitted as the values of targets')

            primary_dir = os.path.dirname(source)
            if '/' in primary_dir or primary_dir == '':
                raise ValueError(f'Must have strictly one level of directory for every source: {source}')
            self.primary_dirs.add(primary_dir)

            data = classify(source)

            self.header_units.update(data['header_units'])
            children = data['post'].copy()
            module = data['module']

            node = Node(None, children, **data)

            if data['type'] == Type.plain:
                self.classes[Type.plain].add(node)
            elif data['type'] == Type.module_impl:
                if module not in self.classes[Type.module_impl]:
                    self.classes[Type.module_impl][module] = [node]
                else:
                    self.classes[Type.module_impl][module] += [node]
            elif data['type'] in [Type.module, Type.module_partition]:
                self.classes[data['type']][module] = node
            else:
                raise RuntimeError(f'Unknown utility.Type. This shouldn\'t have happened: {data["type"]}')

            if index == 0:
                if data['type'] != Type.plain or not source.endswith('.cpp'):
                    raise ValueError('Source represeting the target (i.e. the very first item in the list) must be plain')
                self.root_node = node

        self.classes[Type.plain] = list(self.classes[Type.plain])

        os.chdir('..')

    def load_bakerfile(self):
        if not os.path.exists('Bakerfile.json'):
            raise RuntimeError('Missing Bakerfile.json')

        with open('Bakerfile.json') as bakerfile:
            self.options = {}
            options = json.load(bakerfile)

            if 'targets' not in options:
                raise ValueError('Must specify targets')

            for key in self.options_default:
                if key in options:
                    self.options[key] = {}
                    for subkey in self.options_default[key]:
                        if subkey in options[key]:
                            value = options[key][subkey]
                            value_def = self.options_default[key][subkey]
                            if type(value) != type(value_def):
                                raise TypeError(f'{key} > {subkey} must be of type {type(value_def)}')
                            self.options[key][subkey] = value
                        else:
                            self.options[key][subkey] = self.options_default[key][subkey]
                else:
                    self.options[key] = self.options_default[key]

            for key in options:
                if key not in self.options:
                    self.options[key] = options[key]

    def handle_args(self):
        self.parse_args()
        self.process_args()

    def process_args(self):
        if 'help' in self.args:
            eprint(self.args_nature)
            exit(0)

        if 'default_bakerfile' in self.args:
            print(json.dumps(self.options_default, indent=4))
            exit(0)

        if 'dump_bakerfile' in self.args:
            print(json.dumps(self.options, indent=4))
            exit(0)

        self.show = 'show' in self.args
        self.rebuild = 'rebuild' in self.args

        self.type = 'release' if 'release' in self.args else 'debug'
        self.type_flags = self.options['flags']['release'] if self.type == 'release' else self.options['flags']['debug']
        self.base_flags = self.options['flags']['base']

        self.dirs = {}
        self.dirs['build'] = os.path.join(self.options['dirs']['build'], self.type)
        self.dirs['object'] = os.path.join(self.dirs['build'], self.options['dirs']['object'])
        self.dirs['header_units'] = os.path.join(self.options['dirs']['build'], self.options['dirs']['header_units'])

        self.cxx = [self.options['options']['cxx']]

    def parse_args(self):
        i = 1
        self.args = {}

        while i < len(sys.argv):
            current = sys.argv[i]
            if current in self.args_nature:
                count = self.args_nature[current]
                if i + count >= len(sys.argv):
                    raise ValueError(f'{current} requires {count} arguments')
                self.args[current] = sys.argv[i + 1 : i + count + 1]
                i += count
            else:
                raise ValueError(f'Unknown argument: {current}')
            i += 1

    def run(self, args):
        if self.show:
            eprint(' '.join(args))
        status = subprocess.run(args)
        if status.returncode != 0:
            raise RuntimeError(f'Last command abnormally exited with code {status.returncode}')


if __name__ == '__main__':
    ins = Baker()
