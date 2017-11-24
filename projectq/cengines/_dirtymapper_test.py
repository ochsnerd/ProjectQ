#   Copyright 2017 ProjectQ-Framework (www.projectq.ch)
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

# David Ochsner
# 2017-10-23
# ochsnerd@student.ethz.ch


# TODO: write automatically verifiable tests?


# David Ochsner
# 2017-11-01
# ochsnerd@student.ethz.ch

from projectq import MainEngine
from projectq.cengines import DirtyQubitMapper, DummyEngine
from projectq.backends import Simulator
from projectq.meta import Control, Compute, Uncompute, DirtyQubits
from projectq.ops import X, CNOT,  Measure, ControlledGate, Tensor, H, Toffoli


def test0(dummy):
    """
    Test if 'empty' dqubit gets handled correctly
    """
    engines = [DirtyQubitMapper(verbose=True)]
    eng = MainEngine(dummy, engines)

    dqubit1 = eng.allocate_qubit(dirty = True)
    qubit1 = eng.allocate_qubit()
    

def test1(dummy):
    """
    Test if a command on a dqubit gets remapped correctly
    """
    engines = [DirtyQubitMapper(verbose=True)]
    eng = MainEngine(dummy, engines)

    dqubit1 = eng.allocate_qubit(dirty = True)
    qubit1 = eng.allocate_qubit()
    
    X | dqubit1
    
def test2(dummy):
    """
    Test if a dqubit gets remapped correctly if some clean qubits are involved
    """
    engines = [DirtyQubitMapper(verbose=True)]
    eng = MainEngine(dummy, engines)
    
    dqubit1 = eng.allocate_qubit(dirty = True)
    qubit1 = eng.allocate_qubit()
    qubit2 = eng.allocate_qubit()
    
    CNOT | (dqubit1,qubit1)    

def test3(dummy):
    """
    Test if multiple dqubits are handled correctly
    """
    engines = [DirtyQubitMapper(verbose=True)]
    eng = MainEngine(dummy, engines)


    dqubit1 = eng.allocate_qubit(dirty = True)
    qubit_involved = eng.allocate_qubit()
    dqubit2 = eng.allocate_qubit(dirty = True)
    qubit_uninvolved = eng.allocate_qubit()

    CNOT | (dqubit1, qubit_involved)
    CNOT | (dqubit2, qubit_involved)
    
    eng.deallocate_qubit(dqubit1[0])
    
    X | qubit_involved
    
    eng.deallocate_qubit(dqubit2[0])
    # "problem" now:
    #   CNOTS make qubit_involved involved
    #   dqubit1 gets deallocated and mapped into dqubit2, nothing is sent on as dqubit2 is involved
    #   X acting on qubit_involved gets cached
    #   qubit_involved gets deallocated, but is still involved, so the deallocate gets cached
    #   then dqubit2  is deallocated and gets mapped into qubit_uninvolved. As it is uninvolved,
    #   the commands get sent on (all commands acting on dqubit2 and the commands
    #   influencing other cached ones)
    #   the X-gete on qubit_involved stays behind
    #   the engine deallocates all qubits again as it gets deconstructed
    #   the deallocation on qubit_involved gets cached
    #   the X-gate and the deallocation only get sent on after flush is called
    #   
    #   see similar problem in test4
    #
    #   is this really a problem? yes, if a gate stays cached mistakenly, it can hold up everything
    #   
    #   maybe check in check_and_send if there are active dirtyqubits?
    #   or just don't map into any involved qubits (even though they might not interact with paricular dqubit)?
    
    
    
def test4(dummy):
    """
    Test if Toffoli-Gates are handled correctly
    """
    engines = [DirtyQubitMapper(verbose=True)]
    eng = MainEngine(dummy, engines)
    
    
    qubit1 = eng.allocate_qubit()
    qubit2 = eng.allocate_qubit()
    qubit3 = eng.allocate_qubit()
    dqubit = eng.allocate_qubit(dirty = True)
    
    Toffoli | (dqubit, qubit1, qubit2)
    
def test5(dummy):
    """
    Test targetting
    """
    engines = [DirtyQubitMapper(verbose=True)]
    eng = MainEngine(dummy, engines)
    
    not_target = eng.allocate_qubit()
    target = eng.allocate_qubit()
    
    with DirtyQubits(eng, target):
        dqubit = eng.allocate_qubit(dirty=True)
        X | dqubit
        eng.deallocate_qubit(dqubit[0])

def test6(dummy):
    """
    Test missing deallocation of dirty qubit
    """
    engines = [DirtyQubitMapper(verbose=True)]
    eng = MainEngine(dummy, engines)
    
    not_target = eng.allocate_qubit()
    target = eng.allocate_qubit()
    
    with DirtyQubits(eng, target):
        dqubit = eng.allocate_qubit(dirty=True)
        X | dqubit
        
    eng.deallocate_qubit(dqubit[0])
    
    
if __name__=='__main__':
    dummy = DummyEngine(save_commands=True)
    test5(dummy)
    print("\n\n\n\n\n")
    print("Backend received commands:")
    for cmd in dummy.received_commands:
        print(cmd)

    print("----------------------------DONE-------------------------------")
    print("During cleanup:")

    
    
