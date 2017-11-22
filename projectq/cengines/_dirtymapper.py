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
# 2017-10-18
# ochsnerd@student.ethz.ch
import pdb

from projectq.cengines import BasicEngine
from projectq.meta import DirtyQubitTag
from projectq.ops import   (AllocateQubitGate,
                            AllocateDirtyQubitGate,
                            DeallocateQubitGate,
                            FlushGate,      # can I get them from here?
                            FastForwardingGate)

class DirtyQubitMapper(BasicEngine):
    def __init__(self, verbose=False):
        BasicEngine.__init__(self)
        # Question: What about main_engine.dirty_qubits ? Is this in use?
        # I'm guessing not because DeallocateQubitGate doesn't get the DirtyQubitTag
        # (see cengines/_basics.py: ln149)


        # List of lists, one list per active qubit. Commands (gates) acting on
        # dirty qubits or qubits interacting with dirty qubits are cached
        # The position in the list corresponds to the qubit ID.
        # If the qubit is 'invalid' (it has not been allocated yet or it was 
        # already deallocated), the list is just [-1]
        self._cached_cmds = [] # becomes list of lists
        
        self._verbose = verbose
        self.manualmap = -1

    def is_meta_tag_handler(self, tag):
        if tag == DirtyQubitTag:
            return True
        else:
            return False

    def is_available(self, cmd):
        return True
        
    def _send_qubit_pipeline(self, snd_ID, n):
        """
        Sends out n cached commands acting in the snd_ID-qubit
        """
        cmds = self._cached_cmds[snd_ID] # for readability
        if cmds == [-1]:
            # attempting to send our 'invalid qubit' flag
            self._print("Trying to send our invalid qubit flag")
            return
        
        self._print("Sending on " + str(n) + " commands on qubit " + str(snd_ID))
        sent_deallocate = False # to be able to later invalidate snd_ID if needed
        
        for i in range(min(n,len(cmds))):
            other_involved_qubits = [qb
                                     for qreg in cmds[i].all_qubits
                                     for qb in qreg
                                     if qb.id != snd_ID]

            for qb in other_involved_qubits:
                # index = self._find_index(qb.id)
                other_ID = qb.id
                try:
                    # find position of the cmd in the list of the other qubit
                    cmd_pos = 0
                    while self._cached_cmds[other_ID][cmd_pos] != cmds[i]:
                        cmd_pos += 1
                    
                    # send all commands acting on the other qubit which were 
                    # cached before this cmd
                    self._send_qubit_pipeline(other_ID, cmd_pos)
                    
                    # all previous commands on the other qubit were sent on,
                    # we delete the one we're inspecting now in the other list
                    # to avoid sending it multiple times
                    self._cached_cmds[other_ID] = self._cached_cmds[other_ID][1:]
                except IndexError:
                    self._print("Invalid qubit pipeline encountered (in the"
                          " process of shutting down?).")
            # all commands interacting with our current one have been sent on
            # we can send the current one
            self.send([cmds[i]])
            if isinstance(cmds[i].gate, DeallocateQubitGate):
                sent_deallocate = True
                
        # remove the cmds we just sent on from _cached_cmds
        self._cached_cmds[snd_ID] = self._cached_cmds[snd_ID][n:]
        
        if sent_deallocate:
            # invalidate ID if we sent the deallocation
            assert(self._cached_cmds[snd_ID] == [])
            self._cached_cmds[snd_ID] = [-1]
        
    def _find_remap_qubitID(self, rmp_ID):
        """
        Finds a valid (i.e. not interacting with the dirty qubit) qubit to map
        the qubit with rmp_ID into. If no such qubit is found, returns None
            -prefer qubits indicated by 'with DirtyQubits'
        """
        if self.manualmap != -1:
            return self.manualmap
        
        possible_qubits = [i for i,cmds
                           in enumerate(self._cached_cmds)
                           if cmds != [-1] ]
        possible_qubits.remove(rmp_ID)
        
        for cmd in self._cached_cmds[rmp_ID]:
            other_involved_qubits = [qb
                                     for qreg in cmd.all_qubits
                                     for qb in qreg
                                     if qb.id != rmp_ID]
            for qb in other_involved_qubits:
                if qb.id in possible_qubits:
                    possible_qubits.remove(qb.id)
        
        self._print("Found qubits that we can map into:")
        self._print(possible_qubits)
        
        if possible_qubits == []:
            return None
            
        self._print("Remapping into " + str(possible_qubits[0]))
    
        return possible_qubits[0]
        
    def _remap_dqubit(self, rmp_ID):
        """
        Remaps the operations on deallocated dirty qubit to a qubit not
        interacting with that particular qubit, if such a qubit exists.
        Returns the ID of the qubit it mapped the dirty qubit into (mappee),
        plus whether the qubit mapped into was involved beforehand,
        ie whether the commands have to stay cached or can be sent on
        """
        self._print("Remapping deallocated dqubit")
                    
        assert(self._is_dirty_alloc(self._cached_cmds[rmp_ID][0]))
        
        # No gates performed on dqubit other than allocate/deallocate
        # we just have to clean up
        if self._cached_cmds[rmp_ID][1:-1] == []:
            self._print("Don't have to remap 'empty' dqubit")
            self._cached_cmds[rmp_ID] = [-1]
            return rmp_ID, True # don't send on our invalid flag
        
        new_ID = self._find_remap_qubitID(rmp_ID)
        
        # maybe there is no possible qubit to remap to
        if new_ID == None:
            return rmp_ID, False

        # remove allocate and deallocate command
        self._cached_cmds[rmp_ID] = self._cached_cmds[rmp_ID][1:-1]
        
        # Change ID of qubits of cached commands
        for cmd in self._cached_cmds[rmp_ID]:
            for sublist in cmd.all_qubits:
                for qubit in sublist:
                    if qubit.id == rmp_ID:
                        qubit.id = new_ID
        
        wait = True # set later
        
        # append commands acting on qubit rmp_ID to list of qubit new_ID
        if self._cached_cmds[new_ID] == []:
            self._print("Map to not involved")
            self._cached_cmds[new_ID] = self._cached_cmds[rmp_ID]
            self._cached_cmds[rmp_ID] = [-1]
            wait = False
        else:
            self._print("Map to involved")
            if isinstance(self._cached_cmds[new_ID][-1], DeallocateQubitGate):
                # The qubit we map to is already deallocated, but still cached
                # sneak in the cmds of the dqubit before the deallocation
                self._cached_cmds[new_ID] =(self._cached_cmds[new_ID][:-1]
                                             + self._cached_cmds[rmp_ID]
                                             + self._cached_cmds[new_ID][-1])
            else:
                self._cached_cmds[new_ID].extend(self._cached_cmds[rmp_ID])
            self._cached_cmds[rmp_ID] = [-1]
            wait = True
        
        self._print("After remapping dqubit")
        self.print_state()
        
        return new_ID, wait

    def _check_and_send(self):
        """
        Checks if the last cmds in _cached_cmds are DeallocateQubitGate 
        acting on dirty qubits. If so, they are remapped - if possible -
        and then sent to the next engine
        """
        self._print("Called checkandsend")
        for ID, cmd_list in enumerate(self._cached_cmds):
            if cmd_list == [] or cmd_list == [-1]:
                # no cached cmds or the qubit has already been deallocated
                # or it is not yet allocated - either way we don't do anything
                continue
            last_cmd = self._cached_cmds[ID][-1]
            assert(ID in [qubit.id
                          for sublist in last_cmd.all_qubits
                          for qubit in sublist])
            if (self._is_dirty_alloc(self._cached_cmds[ID][0]) and
                isinstance(last_cmd.gate, DeallocateQubitGate)):
                # a dirty qubit was deallocated and we have it's whole life-
                # time cached - we can try to remap it!
                self._print("Trying to remap " + str(ID))
                
                mappedinto_ID, wait = self._remap_dqubit(ID)
                
                # if we mapped into an involved qubit, we wait.
                # else we send on the cmds
                if not wait:
                    n = len(self._cached_cmds[mappedinto_ID])
                    self._send_qubit_pipeline(mappedinto_ID, n)

    def _cache_cmd(self, cmd):
        """
        Caches a command (adds a copy of the command to each list the qubits it
        acts on)
        """
        id_list = [qubit.id for sublist in cmd.all_qubits for qubit in sublist]
        for ID in id_list:
            self._cached_cmds[ID].append(cmd)

    def _is_involved(self, cmd):
        """
        Checks if cmd acts on an involved qubit
        """
        id_list = [qubit.id for sublist in cmd.all_qubits for qubit in sublist]
        for ID in id_list:
            if (self._cached_cmds[ID] != [] and
                self._cached_cmds[ID] != [-1]):
                return True
        return False

    def _is_dirty_alloc(self, cmd):
        """
        Checks if cmd is allocation of a dirty qubit
        """
        if (DirtyQubitTag() in cmd.tags
            and isinstance(cmd.gate, AllocateQubitGate) # Question: Doing this instead of cmd.gate == AllocateQubitGate() because isinstance works when the class inherits from it?
            or isinstance(cmd.gate, AllocateDirtyQubitGate)): # Question: is this in use?
            return True
        return False

    def receive(self, command_list):
        """
        Receive list of commands from previous compiler engines.
        Commands are sent on unless they interact with a dirty qubit
        """
        for cmd in command_list:
            self._print("Inspecting command:")
            self._print(cmd)
            # updating _cached_cmds
            if isinstance(cmd.gate, AllocateQubitGate):
                new_ID = cmd.qubits[0][0].id
                self._print("Adding qubit " + str(new_ID) + " to cached_cmds")
                self._print(len(self._cached_cmds))
                if len(self._cached_cmds) ==  new_ID:
                    # the qubit is allocated with the consecutive ID
                    self._cached_cmds.append([])
                elif len(self._cached_cmds) > new_ID: 
                    # the allocation gate got delayed in a previous cengine
                    assert(self._cached_cmds[new_ID] == [-1])
                    self._cached_cmds[new_ID] = []
                else:
                    # other allocation gates got delayed in previous cengines -
                    # have to make sure the list indices match up with qubit IDs
                    skipped = new_ID - len(self._cached_cmds)
                    self._cached_cmds.extend([[-1] for _ in range(skipped)])
                    self._cached_cmds.append([])

            if isinstance(cmd.gate,FlushGate):
                # received flush-gate - send on all cached cmds
                self._print("Received FlushGate")
                for ID, cmd_list in enumerate(self._cached_cmds):
                    self._send_qubit_pipeline(ID, len(cmd_list))
            elif self._is_involved(cmd) or self._is_dirty_alloc(cmd):
                # received command involving a qubit already involved or an 
                # allocation of a dirty qubit - cache the command
                self._print("Caching")
                self._cache_cmd(cmd)
                self._check_and_send()
            else:
                # the received command doesn't concern us, we update our list
                # and then send it on
                self._print("Forwarding")
                if isinstance(cmd.gate, DeallocateQubitGate):
                    self._print("Invalidating qubit ID")
                    dealloc_ID = cmd.qubits[0][0].id
                    assert(self._cached_cmds[dealloc_ID] == [])
                    self._cached_cmds[dealloc_ID] = [-1]
                self.send([cmd])
        self.print_state()
        self._print("\n\n")
    
    def _print(self, message):
        if self._verbose:
            print(message)

    def print_state(self):
        """
        Helper for "debugging"
        """
        if not self._verbose:
            return
        print("----------------------------------")
        print("State of dirtymapper:")
        for qubit_id, cmds in enumerate(self._cached_cmds):
            print("  Acting on qubit " + str(qubit_id))
            for cmd in cmds:
                print("    " + str(cmd))
        print("----------------------------------")
