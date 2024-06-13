from utility import Type

class Node:
    def __init__(self, parent, children, **kwargs):
        self.data = kwargs
        self.parent = parent
        self.children = children
        self.check()

    def check(self):
        for required in [('module', str), ('filename', str), ('type', Type)]:
            if required[0] not in self.data:
                raise KeyError(f'Required key {required[0]} not in the node\'s dictionary: {self.data}')
            if type(self.data[required[0]]) != required[1]:
                raise TypeError(f'Value must be of type {required[1]} for key {required[0]} in the node\'s dictionary: {self.data}')

        for child in self.children:
            if type(child) not in [Node, str]:
                raise TypeError(f'Every child must be of type Node/str: {self.data}')

    def __repr__(self):
        s = f'<Node (0x{id(self):x}): parent: ' + self.parent.__repr__()
        s += ', children: ' + str(len(self.children)) + ', data: '
        s += self.data.__repr__() + '>'
        return s

        s = f'<Node (0x{id(self):x}): parent: ' + self.parent.__repr__()
        s += ', children: ['
        for index, child in enumerate(self.children):
            s += child.__repr__()
            if index != len(self.children)-1:
                s += ', '
        s += '], data: '
        s += self.data.__repr__()
        s += '>'
        return s
        
