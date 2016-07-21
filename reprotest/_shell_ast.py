# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright
'''This is partial implementation of an abstract syntax tree (AST) for
the POSIX shell command language from the grammar at
http://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html#tag_18_10
.  It's used exclusively for generating shell scripts from ASTs, which
means that it doesn't include a parser, and only implements the
functionality needed for reprotest's shell scripts, which means that
it doesn't include any superfluous features or alternatives that
construct the same semantics.

The nodes are classes.  Nodes that correspond directly to rules in
POSIX standard's grammar are named using camel-case conversions of the
names in the grammar (e.g. simple_command becomes SimpleCommand).
Each docstring should contain the right-hand side of the grammar
definition for the corresponding node, if any.  Otherwise, the
docstring should contain describe which grammar rules the node
represents and what other types of nodes it's allowed to contain.  All
nodes should contain only other nodes and strings.  Empty fields are
denoted by the empty string.  All nodes must have __slots__ set to the
empty tuple.  Each class has only one overloaded method, __str__,
which should transform the AST into valid shell code.
'''

import collections
import itertools
import shlex


class _SequenceNode(tuple):
    '''Tuple subclass that returns appropriate types from methods.

    This overloads tuple methods so they return the subclass's type
    rather than tuple and provides a nicer __repr__.

    '''

    def __add__(self, other):
        if self.__class__ is other.__class__:
            return self.__class__(itertools.chain(self, other))
        else:
            raise TypeError('Cannot add two shell AST nodes of different types.')
    __iadd__ = __add__

    def __radd__(self, other):
        if self.__class__ is other.__class__:
            return self.__class__(itertools.chain(other, self))
        else:
            raise TypeError('Cannot add two shell AST nodes of different types: %s, %s' % (repr(self), repr(other)))

    def __getitem__(self, index):
        if isinstance(index, slice):
            return self.__class__(super().__getitem__(index))
        else:
            return super().__getitem__(index)

    def __repr__(self):
        return self.__class__.__name__ + super().__repr__()


class BaseNode:
    '''Abstract base class for all nodes.  This class should never be
    instantiated.

    '''
    __slots__ = ()

    def __str__(self):
        '''A generic implementation of __str__ that returns the node's fields
        separated by spaces.'''
        return ' '.join(str(field) for field in self)


class Command(BaseNode):
    '''Abstract base class for command nodes.  This class exists to define
    a type that other classes can refer to to show where any command
    is allowed, and should never be instantiated.

    Grammar rules:
    
    command: simple_command | compound_command | compound_command
    redirect_list | function_definition;

    compound_command: brace_group | subshell | for_clause | case_clause
    | if_clause | while_clause | until_clause;

    '''
    __slots__ = ()


class List(BaseNode, _SequenceNode):
    '''The recursion in this rule is flatted into a sequence.
    separator_op is a & or ;.

    Grammar rules:

    list: list separator_op and_or | and_or;

    compound_list: term | newline_list term | term separator |
    newline_list term separator;

    newline_list: NEWLINE | newline_list NEWLINE;

    separator: separator_op linebreak | newline_list;

    linebreak: newline_list | /* empty */

    Attributes:
        *args (Sequence[Term]): A sequence of commands terminated by & or ;.

    '''
    __slots__ = ()


class Term(BaseNode, collections.namedtuple('_Term', 'command separator')):
    '''This rule is recursive in the grammar, but its direct recursion is
    handled in List in this AST.

    Grammar rule:

    term separator and_or | and_or

    Attributes:
        command (AndList, OrList, Command): A command or sequence of commands.
        separator (str): & or ;.
    '''
    __slots__ = ()

    def __str__(self):
        return str(self.command) + ' ' + self.separator + '\n'


class AndList(BaseNode, _SequenceNode):
    '''While the && and || operators are not associative with each other,
    each is associative with itself, so this recursion can also be
    flattened into a sequence.

    Grammar rules:
    
    list: list separator_op and_or | and_or;

    and_or: pipeline | and_or AND_IF linebreak pipeline
    | and_or OR_IF linebreak pipeline;

    Attributes:
        *args (Sequence[Pipelines, Commands]): A sequence of commands and/or
            pipelines.

    '''
    __slots__ = ()

    def __str__(self):
        return ' && '.join(str(field) for field in self)


class OrList(BaseNode, _SequenceNode):
    '''While the && and || operators are not associative with each other,
    each is associative with itself, so this recursion can also be
    flattened into a sequence.

    Grammar rules:
    
    list: list separator_op and_or | and_or;

    and_or: pipeline | and_or AND_IF linebreak pipeline
    | and_or OR_IF linebreak pipeline;

    Attributes:
        *args (Sequence[Pipelines, Commands]): A sequence of commands and/or
            pipelines.

    '''
    def __str__(self):
        return ' || '.join(str(field) for field in self)


class Pipeline(BaseNode, _SequenceNode):
    '''The recursion in this rule is flatted into a sequence.  The option
    to prepend the bang (!) to a pipeline is deliberately omitted.  It
    would require another class because the __str__ method would be
    different.

    Grammar rules:

    pipeline: pipe_sequence | Bang pipe_sequence;

    pipe_sequence: command | pipe_sequence '|' linebreak command;

    Attributes:
        *args (Sequence[Command]): commands to be piped together.

    '''
    __slots__ = ()

    def __str__(self):
        return ' | '.join(str(field) for field in self)


class SimpleCommand(Command,
                    collections.namedtuple('_SimpleCommand',
                                           'cmd_prefix cmd_name cmd_suffix')):
    '''The rule distinguishes between command names prefixed with
    redirection or environment variables by using cmd_word instead of
    cmd_name, but this difference is immaterial when generating shell
    code from an AST.  cmd_name and cmd_word are just WORDs.

    Grammar rule:

    cmd_prefix cmd_word cmd_suffix | cmd_prefix cmd_word | cmd_prefix |
    cmd_name cmd_suffix | cmd_name;

    Attributes:
        cmd_prefix (CmdPrefix, ''): Environment variables and IO redirection.
        cmd_name (str): A valid shell command name.
        cmd_suffix (CmdSuffix, ''): Command arguments and IO redirection.

    '''
    __slots__ = ()

    def __str__(self):
        return ((str(self.cmd_prefix) + ' ' if self.cmd_prefix else '') +
                str(self.cmd_name) +
                (' ' + str(self.cmd_suffix) if self.cmd_suffix else ''))

    @classmethod
    def make(cls, *args):
        '''Convenience constructor for generating SimpleCommand nodes.'''
        return cls('', args[0], CmdSuffix(args[1:]))


class CmdPrefix(BaseNode, _SequenceNode):
    '''The recursion in this rule is flatted into a sequence.

    Grammar rule:

    io_redirect | cmd_prefix io_redirect | ASSIGNMENT_WORD | cmd_prefix
    ASSIGNMENT_WORD;

    Attributes:
        *args (AssignmentWord, IORedirect): IO redirection and
            environment variables.
    '''
    __slots__ = ()


class AssignmentWord(BaseNode,
                     collections.namedtuple('_AssignmentWord', 'target value')):
    '''Corresponds to environment variable assignments of the form
    target=value.

    Attributes:
        target (str): Environment variable name.
        value (str): Environment variable value.
    '''
    __slots__ = ()

    def __str__(self):
        return str(self.target) + '=' + str(self.value)


class IORedirect(BaseNode,
                 collections.namedtuple('_IORedirect',
                                        'io_number operator filename')):
    '''This represents a redirection and combines three rules.  here_end
    is just a WORD.

    Grammar rules:

    io_redirect: io_file | IO_NUMBER io_file | io_here | IO_NUMBER io_here;
    
    io_file: '<' filename | LESSAND filename | '>' filename | GREATAND filename
    | DGREAT filename | LESSGREAT filename | CLOBBER filename;

    io_here: DLESS here_end | DLESSDASH here_end;

    Attributes:
        io_number (int, ''): A file descriptor.  This should hold the empty
            string if omitted.
        operator (str): One of >, <, <<, >>, <&, >&, <>, <<-, or >|.
        filename (str): Valid file name to redirect to.

    '''
    __slots__ = ()

    def __str__(self):
        return (str(self.io_number) + str(self.operator) + ' ' +
                str(self.filename))


class CmdSuffix(BaseNode, _SequenceNode):
    ''''The recursion in this rule is flatted into a sequence.  This node
    represents the arguments passed to a simple command.

    Grammar rule:

    io_redirect | cmd_suffix io_redirect | WORD | cmd_suffix WORD

    Attributes:
        *args (str, IORedirect): command arguments and IO redirection.

    '''
    __slots__ = ()


class IfClause(Command,
               collections.namedtuple('_IfClause', 'condition then else_part')):
    '''The start of an if-then conditional clause.

    Grammar rule:

    If compound_list Then compound_list else_part Fi

    Attributes:
        condition (List, Command): The command whose exit status determines
            which branch is taken.
        then (List, Command): The command to execute if condition exits with
            zero.
        else_part (ElsePart, ''): The optional command to execute if
            condition exits with a nonzero value.

    '''
    __slots__ = ()

    def __str__(self):
        return ('if ' + str(self.condition) + '\nthen ' + str(self.then) +
                '\n' + str(self.else_part) + '\nfi')


class ElsePart(BaseNode, collections.namedtuple('_IfClause', 'elifs then')):
    '''The elif and else parts of an if-then conditional clause.  The rule
    corresponding to this node is recursive, but the recursion is
    handled in Elifs.

    Grammar rule:

    Elif compound_list Then compound_list | Elif compound_list Then
    compound_list else_part | Else compound_list

    Attributes:
        elifs (Elifs, ''): The optional conditions and commands to execute if
            the preceding command exits with nonzero status.
        then (List, Command, ''): The optional command to execute if
            all other conditionals exit with nonzero statuses.

    '''
    __slots__ = ()

    def __str__(self):
        return (str(self.elifs) +
                ('\nelse ' + str(self.then)) if self.then else '')

class Elifs(BaseNode, _SequenceNode):
    '''This node doesn't directly correspond to a grammar rule.  It
    flattens the recursion for elif statements in else_part.

    Grammar rule:

    Elif compound_list Then compound_list | Elif compound_list Then
    compound_list else_part | Else compound_list

    Attributes:
        *args (Sequence[Elif]): A sequence of elif statements.

    '''
    __slots__ = ()

    def __str__(self):
        return '\n'.join(str(field) for field in self)


class Elif(BaseNode, collections.namedtuple('_Elif', 'condition then')):
    '''This node also doesn't directly correspond to a grammar rule

    Grammar rule:

    Elif compound_list Then compound_list | Elif compound_list Then
    compound_list else_part | Else compound_list

    Attributes:
        condition (List, Command): The command whose exit status determines
            which branch is taken.
        then (List, Command): The command to execute if condition exits with
            zero.

    '''
    __slots__ = ()
    
    def __str__(self):
        return 'elif ' + str(self.condition) + '\nthen ' + str(self.then)


class BraceGroup(Command, collections.namedtuple('_BraceGroup', 'list')):
    '''Grammar rule:

    Lbrace compound_list Rbrace'''
    __slots__ = ()

    def __str__(self):
        return '{ ' + str(self.list) + ' }'


class Subshell(Command, collections.namedtuple('_Subshell', 'list')):
    '''Grammar rule:

    '(' compound_list ')'

    '''
    __slots__ = ()

    def __str__(self):
        return '( ' + str(self.list) + ' )'


class Quote(Command, collections.namedtuple('_Quote', 'command')):
    '''This is a special node that allows nesting of commands using shell
    quoting.  For example, to pass a script to a specific shell:

    SimpleCommand('', 'bash', CmdSuffix([Quote(<script>)]))

    This can also be used to insert shell code from other sources into
    an AST in a proper way.

    Attributes:
        command (List, Command, str): AST or string to quote.

    '''
    __slots__ = ()

    def __str__(self):
        return shlex.quote(str(self.command))
