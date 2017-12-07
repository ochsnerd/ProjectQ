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
from projectq.cengines import BasicEngine
from projectq.types import BasicQubit
from projectq.meta import DirtyQubitTag, DirtyQubitManagementError
from projectq.ops import (AllocateQubitGate,
                          AllocateDirtyQubitGate,
                          DeallocateQubitGate,
                          FlushGate,
                          FastForwardingGate)


class DirtyQubitMapper(BasicEngine):
    def __init__(self,
                 verbose=False,
                 ignore_FastForwarding=False,
                 cache_limit=200):
        BasicEngine.__init__(self)
        
        # information is cached in the following data-structure:
        # a dict with qubit-ids as keys, and a list-int tuple.
        # the list holds all cached commands acting on the qubit, while the int
        # stores the qubit load (the lower this number, the
        # less imbalanced the circuit becomes if operations are mapped into the
        # qubit)
        # Qubits for which the deallocate-command has been sent on are deleted
        # from the dict
        # Example:
        # _cache == {1: ([], 10), 3:([], 2)}
        # no commands are cached on qubit 1 and 3. Qubit 2 has not yet been
        # allocated or was already deallocated. Mapping into qubit 3 would be
        # best.
        self._cache = dict()

        self._ignore_FF = ignore_FastForwarding
        self._verbose = verbose
        self._cache_limit = cache_limit
        self._manualmap = -1

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
        try:
            cmds = self._cache[snd_ID][0]  # for readability
        except KeyError:
            self._print("Trying to send out invalid qubit cache")
            return

        self._print("Sending on " + str(n) +
                    " commands on qubit " + str(snd_ID))
        sent_deallocate = False  # to be able to later invalidate snd_ID

        for i in range(min(n, len(cmds))):
            other_involved_qubits = [qb
                                     for qreg in cmds[i].all_qubits
                                     for qb in qreg
                                     if qb.id != snd_ID]

            for qb in other_involved_qubits:
                # index = self._find_index(qb.id)
                ID = qb.id
                try:
                    # find position of the cmd in the list of the other qubit
                    cmd_pos = 0
                    while self._cache[ID][0][cmd_pos] != cmds[i]:
                        cmd_pos += 1

                    # send all commands acting on the other qubit which were
                    # cached before this cmd
                    self._send_qubit_pipeline(ID, cmd_pos)

                    # all previous commands on the other qubit were sent on,
                    # we delete the one we're inspecting now in the other list
                    # to avoid sending it multiple times
                    self._cache[ID][0][:] = self._cache[ID][0][1:]
                except KeyError:
                    print("Invalid qubit pipeline encountered (in the" +
                          " process of shutting down?).")
            # all commands interacting with our current one have been sent on
            # we can send the current one
            self.send([cmds[i]])
            if isinstance(cmds[i].gate, DeallocateQubitGate):
                sent_deallocate = True

        # remove the cmds we just sent on from _cache
        del self._cache[snd_ID][0][:n]

        if sent_deallocate:
            # invalidate ID if we sent the deallocation
            assert not self._cache[snd_ID][0], (
                   "Invalidating non-empty cache")
            del self._cache[snd_ID]

    def _find_target(self, rmp_ID):
        """
        Finds a valid (i.e. not interacting with the dirty qubit) qubit
        (target) to map the qubit with rmp_ID into.
        If no such qubit is found, returns None
            -prefer qubits indicated by 'with DirtyQubits'
        """
        if self._manualmap != -1:
            t = self._manualmap
            if not t in self._cache:
                raise DirtyQubitManagementError(
                    "The manually provided target qubit has not " +
                    "yet been allocated or was already deallocated")
            self._manualmap = -1
            return t

        def check_involvement(check_ID, j, ID_set):
            """
            Deletes IDs out of the ID_set if they are involved with a command
            after the j-th command on qubit check_ID
            """
            self._print("-------------------------")
            self._print("New call, looking at ID" + str(check_ID) +
                        ", after " + str(j) + "th cmds")
            self._print(ID_set)
            for i, cmd in enumerate(self._cache[check_ID][0]):
                if i <= j:
                    # jumping over already inspected commands
                    continue
                self._print(str(cmd) + "  at " + str(i))
                other_ID_pos = get_cmd_pos(check_ID, i)
                self._print(other_ID_pos)
                for ID, pos in other_ID_pos:
                    if ID in ID_set:
                        self._print("Found involvement with " + str(ID))
                        ID_set.remove(ID)
                    ID_set = check_involvement(ID, pos, ID_set)
            return ID_set

        def get_cmd_pos(ID, i):
            """
            Return a list of tuples containing two ints:
            The ID of  all* qubits involved in the i-th command in qubit ID and
            the position of that command in it's respective _cache-list.
            *EXCLUDES THE ID ITSELF
            Args:
                ID (int): qubit index
                i (int): command position in qubit ID's command list
            """
            cmd = self._cache[ID][0][i]
            other_IDs = [qb.id
                         for qureg in cmd.all_qubits
                         for qb in qureg
                         if qb.id != ID]
            # 1-qubit gate: only gate at index i is involved
            if not other_IDs:
                return []

            # When the same gate appears multiple times, we need to make sure
            # not to match earlier instances of the gate applied to the same
            # qubits. So we count how many there are, and skip over them when
            # looking in the other lists.
            n_identical_to_skip = sum(1
                                      for prv_cmd in self._cache[ID][0][:i]
                                      if prv_cmd == cmd)
            id_pos_pairs = []
            for other_ID in other_IDs:
                ident_idx = [i
                             for i, c in enumerate(self._cache[other_ID][0])
                             if c == cmd]
                id_pos_pairs.append((other_ID, ident_idx[n_identical_to_skip]))
            return id_pos_pairs

        preferred_qubits = set()
        # check if we have preferred targets for this dirty qubit
        for tag in self._cache[rmp_ID][0][0].tags:
            if isinstance(tag, DirtyQubitTag):
                preferred_qubits.update(tag.target_IDs)

        if preferred_qubits:
            # we found preferred targets
            preferred_qubits = check_involvement(rmp_ID,
                                                 0,
                                                 preferred_qubits)
            if preferred_qubits:
                # some of the preferred targets can be mapped into
                self._print("Found preferred, possible qubits: "
                            + str(preferred_qubits))
                self._print("Remapping into " + str(list(preferred_qubits)[0]))
                return list(preferred_qubits)[0]

        possible_qubits = {ID for ID in self._cache}
        possible_qubits.remove(rmp_ID)

        possible_qubits = check_involvement(rmp_ID,
                                            0,
                                            possible_qubits)

        self._print("Found qubits that we can map into: " +
                    str(possible_qubits))

        if not possible_qubits:
            return None

        self._print("Remapping into " + str(list(possible_qubits)[0]))

        return list(possible_qubits)[0]

    def _remap_dqubit(self, rmp_ID):
        """
        Remaps the operations on deallocated dirty qubit to a qubit not
        interacting with that particular qubit (target), if such a qubit
        exists. Returns the ID of the target, plus whether the target was
        involved beforehand, ie whether the commands have to stay cached or
        can be sent on
        """
        self._print("Remapping deallocated dqubit")

        assert self._is_dirty_alloc(self._cache[rmp_ID][0][0])
        assert self._is_dirty_dealloc(self._cache[rmp_ID][0][-1])

        # No gates performed on dqubit other than allocate/deallocate
        # we just have to clean up
        if self._cache[rmp_ID][0][1:-1] == []:
            self._print("Don't have to remap 'empty' dqubit")
            del self._cache[rmp_ID]
            return rmp_ID, True  # don't have to send anything

        self._print("####################################################")
        new_ID = self._find_target(rmp_ID)
        self._print("####################################################")
        # maybe there is no possible qubit to remap to
        if new_ID is None:
            return rmp_ID, False

        # remove allocate and deallocate command
        self._cache[rmp_ID][0][:] = self._cache[rmp_ID][0][1:-1]

        # Change ID of qubits of cached commands
        for cmd in self._cache[rmp_ID][0]:
            for sublist in cmd.all_qubits:
                for qubit in sublist:
                    if qubit.id == rmp_ID:
                        qubit.id = new_ID

        wait = True  # set later


        # update load on target qubit


        # append commands acting on qubit rmp_ID to list of qubit new_ID
        if not self._cache[new_ID][0]:
            self._print("Map to not involved")
            self._cache[new_ID][0][:] = self._cache[rmp_ID][0]
            del self._cache[rmp_ID]
            wait = False
        else:
            self._print("Map to involved")
            if isinstance(self._cache[new_ID][0][-1].gate,
                          DeallocateQubitGate):
                # The qubit we map to is already deallocated, but still cached
                # sneak in the cmds of the dqubit before the deallocation
                self._cache[new_ID][0][:] = (self._cache[new_ID][0][:-1]
                                             + self._cache[rmp_ID][0]
                                             + [self._cache[new_ID][0][-1]])
            else:
                self._cache[new_ID][0].extend(self._cache[rmp_ID][0])
            del self._cache[rmp_ID]
            wait = True

        self._print("After remapping dqubit")
        self.print_state()

        return new_ID, wait

    def _check_and_send(self):
        """
        Checks the state of _cache:
        -   If there is a dirty qubit that we can remap (both dirty allocate
            and deallocate cached), then it does that
        -   If we don't ignore FastForwardingGates, these are sent on
        -   If we exceed the cache-limit, commands are sent on
        """
        self._print("Called checkandsend")
        # Note on structure:
        # We loop over a dict while possibly removing keys from it.
        # To make sure we handle every entry, each time a key is deleted,
        # the loop is restarted. This is achieved by:
        #   while True
        #       for key in mydict:
        #           if condition(key):
        #               del mydict[key]
        #               break
        #       else:
        #           break
        # Each time the if-block is executed, we break out of the for-loop
        # to the end of the while statement (skipping the else). When the
        # condition evaluates to False every iteration of one for-loop, the
        # else-statement is evaluated and we break out of the while-loop.
        while True:
            for ID, (cmd_list, _) in self._cache.items():
                if not cmd_list:
                    # no cached cmds - we don't have to check anything
                    continue

                if self._is_dirty_dealloc(cmd_list[-1]):
                    wait = False
                    mappedinto_ID = ID
                    if self._is_dirty_alloc(cmd_list[0]):
                        self._print("Trying to remap " + str(ID))
                        # a dirty qubit was deallocated and we have it's whole
                        # lifetime cached - we can try to remap it!
                        mappedinto_ID, wait = self._remap_dqubit(ID)

                    # if we mapped into an involved qubit, we wait.
                    # else we send on the cmds
                    if not wait:
                        n = len(self._cache[mappedinto_ID][0])
                        self._send_qubit_pipeline(mappedinto_ID, n)
                    
                    # by remapping the dirty qubit, we changed the size of the
                    # dict we were looping though - we have to  stop and restart
                    break
                if not cmd_list:
                    # dirty qubit was remapped - no more cached cmds acting
                    # on this qubit
                    continue

                if isinstance(cmd_list[-1].gate, FastForwardingGate) and \
                   not self._ignore_FF:
                    self._send_qubit_pipeline(ID, len(cmd_list))
                    break

                if len(cmd_list) > self._cache_limit:
                    self._send_qubit_pipeline(ID, len(cmd_list))
                    break
            else:
                break

    def _cache_cmd(self, cmd):
        """
        Caches a command (adds a copy of the command to each list the qubits it
        acts on)
        """
        id_list = [qubit.id for sublist in cmd.all_qubits for qubit in sublist]
        for ID in id_list:
            self._cache[ID][0].append(cmd)

    def _is_involved(self, cmd):
        """
        Checks if cmd acts on an involved qubit
        """
        id_list = [qubit.id for sublist in cmd.all_qubits for qubit in sublist]
        for ID in id_list:
            if self._cache[ID][0]:
                return True
        return False

    def _is_dirty_alloc(self, cmd):
        """
        Checks if cmd is allocation of a dirty qubit
        """
        if any(isinstance(tag, DirtyQubitTag) for tag in cmd.tags) \
           and isinstance(cmd.gate, AllocateQubitGate):
            return True
        return False

    def _is_dirty_dealloc(self, cmd):
        """
        Checks if cmd is deallocation of a dirty qubit
        """
        if any(isinstance(tag, DirtyQubitTag) for tag in cmd.tags) \
           and isinstance(cmd.gate, DeallocateQubitGate):
            return True
        return False

    def receive(self, command_list):
        """
        Receive list of commands from previous compiler engines.
        Commands are sent on unless they interact with a dirty qubit
        """
        for cmd in command_list:
            self._print("Inspecting command:")
            self._print(str(cmd) + ", tags: " + str(cmd.tags))

            # updating _cache
            if isinstance(cmd.gate, AllocateQubitGate):
                new_ID = cmd.qubits[0][0].id
                self._print("Adding qubit " + str(new_ID) + " to cached_cmds")
                self._print(len(self._cache))
                self._cache[new_ID] = [], 0
                
            # update qubit load

            if isinstance(cmd.gate, FlushGate):
                # received flush-gate - send on all cached cmds
                self._print("Received FlushGate")
                for ID in list(self._cache):
                    cmds_to_send = len(self._cache[ID][0])
                    self._send_qubit_pipeline(ID, cmds_to_send)
            elif self._is_involved(cmd) or self._is_dirty_alloc(cmd):
                # received command involving a qubit already involved or an
                # allocation of a dirty qubit - cache the command
                self._print("Caching")
                self._cache_cmd(cmd)
                self._check_and_send()
            else:
                # the received command doesn't concern us, we update our cache
                # and then send it on
                self._print("Forwarding")
                if isinstance(cmd.gate, DeallocateQubitGate):
                    self._print("Invalidating qubit ID")
                    dealloc_ID = cmd.qubits[0][0].id
                    try:
                        cmds, _ = self._cache.pop(dealloc_ID)
                    except KeyError:
                        raise DirtyQubitManagementError(
                            "A qubit which was not allocated was deallocated")
                    assert not cmds, "Non-empty cache got deleted"
                self.send([cmd])
        self.print_state()
        self._print("\n\n")
        
    def set_next_target(self, qubit):
        """
        Manually set the next target. The next dirty qubit that gets
        deallocated will be mapped into the provided qubit.
        WARNING: No involvement-check is performed. If the dirty qubit 
        interacts with the provided target, the remapping may change the
        behaviour of the circuit!
        Args:
            qubit (Qubit object): target qubit
        """
        assert(not isinstance(qubit, tuple))
        if isinstance(qubit, BasicQubit):
            self._manualmap = qubit.id
        else:
            self._manualmap = qubit[0].id

    """
    Helpers for "debugging"
    """
    def _print(self, message):
        if self._verbose:
            print(message)

    def print_state(self):
        if not self._verbose:
            return
        print("----------------------------------")
        print("State of dirtymapper:")
        for qubit_id, (cmds, cost) in self._cache.items():
            print("  Qubit " + str(qubit_id))
            print("    Load: " + str(cost))
            print("    Cached Commands:")
            for cmd in cmds:
                print("      " + str(cmd))
        print("----------------------------------")
