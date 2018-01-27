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
                          FastForwardingGate,
                          NotInvertible)
from projectq.ops._gates import *

"""
-   Extend default_costs
-   test shor: a=2 (not random), n=15 -> 0, 1/4, 1/2, 3/4 with equal prob

-   IMPLEMENT TESTS FOR _DIRTYCONSTANTMATH
-   IMPLEMENT TESTS FOR _DIRTYMAPPERs - data-structure
-   update docs
-   remove verbose
"""


class DirtyQubitMapper(BasicEngine):
    """
    DirtyQubitMapper is a compiler engine that attempts to remap dirty qubits
    into other qubits to reduce the width of the circuit.

    A dirty qubit is a qubit that:\n
    - Can be allocated in a general state\n
    - Is in exactly the same state when it gets allocated and deallocated

    Both of these conditions can not be feasibly checked by the engine, so
    the user is responsible to ensure they are met. If a qubit meets the
    conditions, it can be flagged as dirty when it is allocated.

    Example:
            .. code-block:: python

                    eng.allocate_qubit(dirty=True)

    The DirtyQubitMapper then caches all gates acting on that qubit as well
    as all gates acting on qubits influenced by that qubit (for example gates
    on a qubit after a CNOT controlled on the dirty qubit acted on it).
    The size of this cache can be controlled by the cache_limit-Argument. A
    bigger cache can lead to longer compilation times, while a smaller one can
    prevent DirtyQubitMapper from remapping.

    There is also the possibility to specify whether FastForwardingGates are
    cached or sent on. Not caching them means that some dirty qubits will
    not be remapped, while caching them can lead to suboptimal results of
    other compiler engines.

    After the deallocation gate on the dirty qubit is received, the
    DirtyQubitMapper will search for qubits that are not influenced by the
    dirty qubit and remap all gates acting on the dirty qubit into that qubit
    (due to the two conditions on dirty qubits this results in an equivalent
    circuit requiring one less qubit).

    The DirtyQubitMapper also estimates the load on each qubit in order to be
    able to choose a good target for remapping (avoid "serialization" by always
    mapping into to the same qubit).

    To this end, a dict of {GateClass: Cost} can be given,
    which indicates the cost of perforimg each gate (this is dependent on
    which infrastructure the circuit is performed).
    """
    def __init__(self, verbose=False, cache_limit=200, gate_costs=None):
        """
        Initialize a DirtyQubitMapper object.

        Args:
            cache_limit (int): controls how many gates per qubit are cached
            gate_costs (dict): Indicates the cost of an operation.\n
                Key: BasicGate object, Value: Int\n
                If a gate is not found in the dict, the cost of it's inverse
                will be used. If that isn't available, the cost of
                gate_costs[BasicGate] is used. If this is also not available, a
                cost of 1 will be assumed.
        """
        BasicEngine.__init__(self)

        self._cache = dict()

        self._verbose = verbose
        self._cache_limit = cache_limit
        self._manualmap = -1

        if gate_costs is None:
            self._default_cost = 1
            self._gate_costs = self._default_costs()
        else:
            try:
                self._default_cost = gate_costs[BasicGate]
            except KeyError:
                self._default_cost = 1
            self._gate_costs = gate_costs

    def is_meta_tag_handler(self, tag):
        if tag == DirtyQubitTag:
            return True
        else:
            return False

    def is_available(self, cmd):
        return True

    def _default_costs(self):
        """
        Returns a dict containing default costs for common gates
        """
        costs = dict()

        costs[HGate] = self._default_cost
        costs[XGate] = self._default_cost

        return costs

    def _get_gate_cost(self, gate):
        """
        Get the cost of a gate. First looks for an entry to the gate in the
        _gate_costs-dict. Then it looks for the inverse of that gate. If
        nothing is found aswell, the default cost is returned.
        Args:
            gate (Gate object): get by cmd.gate
        Returns:
            (int) The cost of gate
        """
        try:
            gate_cost = self._gate_costs[type(gate)]
        except KeyError:
            try:
                gate_cost = self._gate_costs[type(gate.get_inverse())]
            except (KeyError, NotInvertible):
                gate_cost = self._default_cost
        return gate_cost

    def _send_qubit_pipeline(self, snd_ID, n):
        """
        Sends out the first n cached commands acting in the snd_ID-qubit
        """
        try:
            cmds = self._cache[snd_ID].cmds
        except KeyError:
            self._print("Trying to send out invalid qubit cache")
            return

        self._print("Sending on " + str(n) +
                    " commands on qubit " + str(snd_ID))
        sent_deallocate = False  # to be able to later invalidate snd_ID

        for _ in range(min(n, len(cmds))):
            if not cmds:
                # we already sent on all cmds
                break
            other_IDs_abs_pos = self._get_cmd_pos(snd_ID, 0)

            for ID, abs_pos in other_IDs_abs_pos:
                try:
                    # Find the cache position of the cmd in the list of
                    # the other qubit
                    rel_pos = abs_pos - self._cache[ID].n_cmds_sent

                    # Send all previous cached cmds acting on the other qubit
                    self._send_qubit_pipeline(ID, rel_pos)

                    # All previous commands on the other qubit were sent on,
                    # we delete the one we're inspecting now in the other list
                    # to avoid sending it multiple times
                    if self._cache[ID].cmds[0] == self._cache[snd_ID].cmds[0]:
                        # Only delete first cmd in IDs cache if it really
                        # is the gate we're sending (In multi-controlled gates
                        # the command could've been deleted already by sending
                        # from another controller qubit)
                        self._cache[ID].sent_cmd()
                except KeyError:
                    print("Invalid qubit pipeline encountered (in the" +
                          " process of shutting down?).")
            # all commands interacting with our current one have been sent on
            # we can send the current one
            snd_cmd = self._cache[snd_ID].sent_cmd()
            self.send([snd_cmd])
            if isinstance(snd_cmd.gate, DeallocateQubitGate):
                sent_deallocate = True

        if sent_deallocate:
            # invalidate ID if we sent the deallocation
            assert not self._cache[snd_ID].cmds, (
                   "Attempting to delete non-empty cache")
            del self._cache[snd_ID]

    def _get_cmd_pos(self, ID, i):
        """
        Return a list of tuples containing two ints:
        The ID of  all* qubits involved in the i-th command (RELATIVE position)
        in qubit ID and the ABSOLUTE position of that command in the circuit.
        Get the command with the cmd_at_abs function of QubitHistory.
        *EXCLUDES THE ID ITSELF
        Args:
            ID (int): qubit index
            i (int): command position in qubit ID's command list
        Returns:
            (list of tuples) [(ID, pos),(ID, pos)]
        """
        try:
            return [(Id, abs_pos)
                    for Id, abs_pos in zip(self._cache[ID].inv_ids[i],
                                           self._cache[ID].abs_positions[i])
                    if ID != Id]
        except:
            print("Want abs_pos of cmd {} on qb {}".format(i, ID))

    def _find_target(self, rmp_ID):
        """
        Finds a valid (i.e. not interacting with the dirty qubit) qubit
        (target) to map the qubit with rmp_ID into.
        Prefers qubits indicated by the DirtyQubitTag.
        Also prefers (weaker) qubits with less load.
        If _manualmap was set, uses that as target WITHOUT CHECKING INVOLVEMENT
        and resets _manualmap after.
        If no manualmap was set and no uninvolved qubit is found, returns None

        Args:
            rmp_ID (int): ID of the qubit to remap
        Returns:
            (int) or None; ID of the most suitable remap target
        Raises:
            DirtyQubitManagementError:
                If the provided target is not active, i.e. it hasn't been
                allocated or was already deallocated
        """
        if self._manualmap != -1:
            t = self._manualmap
            if t not in self._cache:
                raise DirtyQubitManagementError(
                    "The manually provided target qubit has not " +
                    "yet been allocated or was already deallocated")
            self._manualmap = -1
            return t

        def check_involvement(check_ID, j, uninv_IDs, checked_IDs, depth=0):
            """
            Deletes IDs out of the uninv_IDs if they are involved with a
            command after the j-th command cached on qubit check_ID
            (relative pos, not absolute pos)

            Args:
                check_ID (int)      : ID of the qubit to check involvement with
                j (int):              relative position in the cache of
                                      check_ID after which involvement is
                                      of interest
                uninv_IDs (set)     : set of qubit IDs (int) to remove involved
                                      qubits from
                checked_IDS (set)   : set of qubit IDs (int) which contains
                                      the IDs of qubits that are already
                                      checked for involvement
            Returns:
                (set(int)) uninv_IDs, but all IDs of involved qubits are removed
            """
            # "Manual" control of recursion depth with info-dump if exceeded
            if depth > 250:
                print(uninv_IDs)
                print(checked_IDs)
                raise DirtyQubitManagementError
            if not uninv_IDs:
                # There are no uninvolved qubits - don't have to check further
                return uninv_IDs
            for i, cmd in enumerate(self._cache[check_ID].cmds[j+1:], j+1):
                other_ID_pos = self._get_cmd_pos(check_ID, i)
                for ID, abs_pos in other_ID_pos:
                    if ID in uninv_IDs:
                        # We found involvement - remove it from set
                        uninv_IDs.remove(ID)
                    depth += 1
                    if ID not in checked_IDs:
                        # The qubit with id ID hasn't been checked earlier -
                        # add it to checked list and check it
                        checked_IDs.add(ID)
                        rel_pos = abs_pos - self._cache[ID].n_cmds_sent
                        if rel_pos < 0:
                            print(ID)
                            print(other_ID_pos)
                            print(self._cache[ID].n_cmds_sent)
                        uninv_IDs = check_involvement(ID, rel_pos, uninv_IDs,
                                                      checked_IDs, depth)
            return uninv_IDs

        def get_lowest_load_id(possible_ids):
            """
            Returns the lowest load on qubits indicated in possible IDs
            Args:
                possible_ids (set(int)): Set of IDs of qubits to find the one
                    with the lowest load from
            Returns:
                (int) ID of the qubit with the lowest load
            """
            lowest_load = None
            corresponding_id = -1
            for ID in possible_ids:
                load = self._cache[ID].load_now()
                if lowest_load is None:
                    lowest_load = load
                    corresponding_id = ID
                elif lowest_load > load:
                    lowest_load = load
                    corresponding_id = ID
            return corresponding_id

        preferred_qubits = set()
        # check if we have preferred targets for this dirty qubit
        for tag in self._cache[rmp_ID].cmds[0].tags:
            if isinstance(tag, DirtyQubitTag):
                # maybe there are IDs in target_IDs that are not active
                preferred_qubits = {ID for ID in tag.target_IDs
                                    if ID in self._cache}

        if preferred_qubits:
            # we found preferred targets
            preferred_qubits = check_involvement(rmp_ID,
                                                 0,
                                                 preferred_qubits,
                                                 set())
            if preferred_qubits:
                # some of the preferred targets can be mapped into
                self._print("Found preferred, possible qubits: "
                            + str(preferred_qubits))
                self._print("Remapping into " + str(get_lowest_load_id(preferred_qubits)))
                return get_lowest_load_id(preferred_qubits)

        possible_qubits = {ID for ID in self._cache}
        possible_qubits.remove(rmp_ID)

        possible_qubits = check_involvement(rmp_ID,
                                            0,
                                            possible_qubits,
                                            set())

        self._print("Found qubits that we can map into: " +
                    str(possible_qubits))

        if not possible_qubits:
            return None

        self._print("Remapping into " + str(get_lowest_load_id(possible_qubits)))

        return get_lowest_load_id(possible_qubits)

    def _move_cache(self, old_ID, new_ID, upd_start):
        """
        Migrates data (cmds, inv_ids, abs_positions, load) from old_ID to
        new_ID.
        Also updates abs_positions and load to reflect the new position of
        the cmds on the new qubit.

            Args:
                old_ID (int)    : ID of the qubit from where we take the data
                                  (deallocated dirty qubit)
                new_ID (int)    : ID of the qubit that we move the data to
                                  (target qubit)
                upd_start (int) : Position in list (relative position)
                                  of last correct load and abs_pos. Can be -1
                                  if there are no cmds cached, so there is no
                                  correct load
        """
        def update_load(upd_ID, strt_pos):
            """
            Update the load on upd_ID after start_pos
            """
            self._print("Updating qubit " + str(upd_ID))
            strt_pos += 1
            for curr_pos, cmd in enumerate(self._cache[upd_ID].cmds[strt_pos:],
                                           strt_pos):
                # The true cost is either the load of this qubit before
                # the current cmd plus the cost of the command OR the load
                # on the other involved qubits (if there are any) after the
                # command, whichever is bigger
                self._print("  Looking at " + str(cmd) + " at pos "
                            + str(curr_pos))
                # Find the load on this qubit
                load_this = self._get_gate_cost(cmd.gate)
                if curr_pos == 0:
                    # We look at the first command cached - look at load of
                    # cmds already sent on
                    load_this += self._cache[upd_ID].load_uncached
                else:
                    # The load before this cmd can be found in the
                    # load-cache
                    load_this += self._cache[upd_ID].loads[curr_pos-1]
                self._print("  load_this = " + str(load_this))

                other_ID_pos = self._get_cmd_pos(upd_ID, curr_pos)
                if other_ID_pos:  # There are other qubits involved in cmd
                    self._print("  Other qubits involved")
                    # The load on the other qubits should be all the same,
                    # but since we put in a placeholder for the target,
                    # we have to make sure to get the correct value
                    try:
                        load_others = max([self._cache[ID].load_at_abs(pos)
                                          for ID, pos in other_ID_pos])
                    except:
                        for i, hist in self._cache.items():
                            hist.print_state(i)
                        raise
                    self._print("  load_others = " + str(load_others))
                    if load_this > load_others:
                        # the qubit we mapped into has a bigger load
                        # than the others -> we have to update the others
                        for ID, abs_pos in other_ID_pos:
                            self._cache[ID].set_load_at_abs(abs_pos, load_this)
                            rel_pos = abs_pos - self._cache[ID].n_cmds_sent
                            update_load(ID, rel_pos)
                    else:
                        # The new qubit has a smaller load than the others
                        # -> we only have to update the load on the new qb
                        load_this = load_others

                self._cache[upd_ID].loads[curr_pos] = load_this
                self._print("  Setting " + str(cmd) + " to " + str(load_this))

        # Change ID in cached cmds and in all inv_ids-list 
        old_hist = self._cache[old_ID]
        for ID_l, abs_position_l in zip(old_hist.inv_ids,
                                      old_hist.abs_positions):
            for ID, abs_pos in zip(ID_l, abs_position_l):
                cmd = self._cache[ID].cmd_at_abs(abs_pos)
                for sublist in cmd.all_qubits:
                    for qubit in sublist:
                        if qubit.id == old_ID:
                            qubit.id = new_ID
            for i, ID in enumerate(ID_l):
                if ID == old_ID:
                    ID_l[i] = new_ID

        # remove allocate and deallocate command
        self._cache[old_ID].cmds = self._cache[old_ID].cmds[1:-1]
        self._cache[old_ID].inv_ids = self._cache[old_ID].inv_ids[1:-1]
        self._cache[old_ID].abs_positions = (
            self._cache[old_ID].abs_positions[1:-1])

        if (self._cache[new_ID].cmds and
             isinstance(self._cache[new_ID].cmds[-1].gate,
                        DeallocateQubitGate)):
            # The qubit we map to is already deallocated, but still cached
            # sneak in the cmds and inv_ids of the dqubit before the
            # deallocation
            upd_start -= 1
            self._cache[new_ID].cmds.insert(-2, self._cache[old_ID].cmds)
            self._cache[new_ID].inv_ids.insert(-2,
                self._cache[old_ID].inv_ids)
            # Has to be updated after
            self._cache[new_ID].abs_positions.insert(-2,
                self._cache[old_ID].abs_positions)
        else:
            # Can just copy the contents over
            self._cache[new_ID].cmds.extend(self._cache[old_ID].cmds)
            self._cache[new_ID].inv_ids.extend(self._cache[old_ID].inv_ids)
            # Has to be updated after
            self._cache[new_ID].abs_positions.extend(
                self._cache[old_ID].abs_positions)

        # For each moved cmd, set the correct abs_position and update it in the
        # other instances of the command
        self._print("||||||||||||||||||||||||||||||||||||||||||||||||||||||||")
        if self._verbose:
            for i, h in self._cache.items():
                h.print_state(i)
        def p():
            if not self._verbose:
                return
            print("3: " + str(self._cache[3].abs_positions[0]))
            print("4: " + str(self._cache[4].abs_positions[0]))

        for i in range(upd_start+1, len(self._cache[new_ID].cmds)):
            new_abs_pos = i + self._cache[new_ID].n_cmds_sent

            # Find pos of new_ID in inv_ids-element == pos of new_ID in
            # abs_positions-element (which is the entry we want to update)
            index = self._cache[new_ID].inv_ids[i].index(new_ID)
            p()
            self._print(index)

            self._cache[new_ID].abs_positions[i][index] = new_abs_pos

            p()

            for j, ID in enumerate(self._cache[new_ID].inv_ids[i]):
                rel_pos = (self._cache[new_ID].abs_positions[i][j] -
                           self._cache[ID].n_cmds_sent)
                self._cache[ID].abs_positions[rel_pos][index] = new_abs_pos
                p()
        if self._verbose:
            for i, h in self._cache.items():
                h.print_state(i)
        self._print("||||||||||||||||||||||||||||||||||||||||||||||||||||||||")

        # Add "empty" entries into load-history to be updated
        self._cache[new_ID].loads += [-1] * len(self._cache[old_ID].cmds)

        update_load(new_ID, upd_start)

        del self._cache[old_ID]

    def _remap_dqubit(self, rmp_ID):
        """
        Remaps the operations on a deallocated dirty qubit to a qubit not
        interacting with that particular qubit (target), if such a qubit
        exists and the whole lifecycle of the dirty qubit is cached.
        Returns the ID of the target and updates the loads that get changed
        after remapping.

        Args:
            rmp_ID (int): ID of dirty qubit to remap
        Returns:
            int Returns the ID of the qubit that the dirty qubit was
                mapped into (target). If there is no possible idle qubit to
                map into, returns rmp_ID.If there is no need to remap
                (because there act no gates on the dqubit other than
                alloc/dealloc), none is returned.
        """
        self._print("Remapping deallocated dqubit")

        rmp_hist = self._cache[rmp_ID]
        rmp_cmds = rmp_hist.cmds

        assert self._is_dirty_alloc(rmp_cmds[0])
        assert self._is_dirty_dealloc(rmp_cmds[-1])

        # No gates performed on dqubit other than allocate/deallocate
        # we just have to clean up
        if rmp_cmds[1:-1] == []:
            self._print("Don't have to remap 'empty' dqubit")
            del self._cache[rmp_ID]
            return None

        new_ID = self._find_target(rmp_ID)

        # maybe there is no possible qubit to remap to
        if new_ID is None:
            return rmp_ID

        # determines where the load-update starts (is the relative position
        # of the last cmd with correct load)
        upd_start = None

        if not self._cache[new_ID].cmds:
            # Mapping into a not involved qubit
            self._print("Map to not involved")
            upd_start = -1  # not an invalid flag, but actual pos
        else:
            # Mapping into an involved qubit
            self._print("Map to involved")
            upd_start = len(self._cache[new_ID].cmds) - 1

        try:
            self._move_cache(rmp_ID, new_ID, upd_start)

            for ID in self._cache.keys():
                self._cache[ID].check_invariants(ID)
        except:
            print(rmp_ID)
            print(new_ID)
            print(upd_start)
            raise

        self._print("After remapping dqubit")
        self.print_state()

        return new_ID

    def _check_and_send(self, ids_to_check):
        """
        Checks the state of ids_to_check in _cache:
        -   If there is a dirty qubit that we can remap (both dirty allocate
            and deallocate cached), then it does that. If there is no other
            involvement, the commands are sent on to the next engine
        -   If we send on the cache of a qubit if a FastForwarding-gate acts
            on it
        -   If we exceed the cache-limit, commands are sent on
        """
        self._print("Called checkandsend, checking: " + str(ids_to_check))

        for ID in ids_to_check:
            cmd_list = self._cache[ID].cmds

            try:
                self._cache[ID].check_invariants()  # COSTLY - REMOVE IF SURE
            except AssertionError:
                self.print_state()
                raise

            if (self._is_dirty_dealloc(cmd_list[-1]) and
                 self._is_dirty_alloc(cmd_list[0])):
                self._print("Trying to remap " + str(ID))
                # a dirty qubit was deallocated and we have it's whole
                # lifetime cached - we can try to remap it!
                new_ID = self._remap_dqubit(ID)
                if new_ID is None:
                    # The dirty qubit had no other cmds than alloc/dealloc
                    continue
                elif new_ID != ID:
                    # The dirty qubit was remapped, we dont have to check
                    # it further because it's gone.
                    # We check the qubit that it was mapped into (mainly to
                    # make sure the cache-max is respected)
                    self._check_and_send([new_ID])
                    continue

            if isinstance(cmd_list[-1].gate, FastForwardingGate):
                self._send_qubit_pipeline(ID, len(cmd_list))

            if len(cmd_list) > self._cache_limit:
                self._send_qubit_pipeline(ID, len(cmd_list)//2)

    def _get_cmd_IDs(self, cmd, exclude=None):
        """
        Returns a list of the IDs of all qubits involved in cmd,
        excluding the qubit with ID exclude
        """
        if exclude is None:
            return [qb.id
                    for qureg in cmd.all_qubits
                    for qb in qureg]
        else:
            return [qb.id
                    for qureg in cmd.all_qubits
                    for qb in qureg
                    if qb.id != exclude]

    def _cache_cmd(self, cmd):
        """
        Caches a command (adds a copy of the command to each list the qubits it
        acts on)
        Returns a list of qubit ids where it added the cmd
        """
        ID_list = self._get_cmd_IDs(cmd)

        abs_cmd_pos = []
        max_load = 0
        for ID in ID_list:
            # Find the absolute position of the cmd on each Qubit
            abs_cmd_pos.append(len(self._cache[ID].cmds) +
                               self._cache[ID].n_cmds_sent)
            # Find the load on the qubits after executing the cmd
            if self._cache[ID].load_now() > max_load:
                max_load = self._cache[ID].load_now()
        max_load += self._get_gate_cost(cmd.gate)

        # Add the cmd to the cache of each qubit
        for ID in ID_list:
            self._cache[ID].add(cmd, ID_list, abs_cmd_pos, max_load)

        return ID_list

    def _is_involved(self, cmd):
        """
        Checks if cmd acts on an involved qubit
        """
        ID_list = self._get_cmd_IDs(cmd)
        for ID in ID_list:
            if self._cache[ID].cmds:
                return True
        return False

    def _is_dirty_alloc(self, cmd):
        """
        Checks if cmd is allocation of a dirty qubit
        """
        if (any(isinstance(tag, DirtyQubitTag) for tag in cmd.tags)
                and isinstance(cmd.gate, AllocateQubitGate)):
            return True
        return False

    def _is_dirty_dealloc(self, cmd):
        """
        Checks if cmd is deallocation of a dirty qubit
        """
        if (any(isinstance(tag, DirtyQubitTag) for tag in cmd.tags)
                and isinstance(cmd.gate, DeallocateQubitGate)):
            return True
        return False

    def receive(self, command_list):
        """
        Receive list of commands from previous compiler engines.
        Commands are sent on unless they interact with a dirty qubit

        Args:
            command_list (list<Command>): List of commands to receive

        Raises:
            DirtyQubitManagementError:
                If a qubit deallocation gate on a qubit is received before it's
                allocation gate.ls
        """
        try:
            for cmd in command_list:
                self._print("Inspecting command:")
                self._print(str(cmd) + ", tags: " + str(cmd.tags))

                # updating _cache
                if isinstance(cmd.gate, AllocateQubitGate):
                    new_ID = cmd.qubits[0][0].id
                    self._print("Adding qubit " + str(new_ID) + " to cached_cmds")
                    self._print(len(self._cache))
                    self._cache[new_ID] = QubitHistory()

                if isinstance(cmd.gate, FlushGate):
                    # received flush-gate - send on all cached cmds
                    self._print("Received FlushGate")
                    for ID in list(self._cache):
                        cmds_to_send = len(self._cache[ID].cmds)
                        self._send_qubit_pipeline(ID, cmds_to_send)
                elif self._is_involved(cmd) or self._is_dirty_alloc(cmd):
                    # received command acting on a qubit already involved or an
                    # allocation of a dirty qubit -> cache the command
                    self._print("Caching")
                    added_to = self._cache_cmd(cmd)
                    self._check_and_send(added_to)
                else:
                    self._print("Forwarding")
                    # the received command doesn't concern us, we update our cache
                    # and then send it on:
                    # update QubitHistories
                    involved_IDs = self._get_cmd_IDs(cmd)
                    gate_cost = self._get_gate_cost(cmd.gate)
                    for ID in involved_IDs:
                        self._cache[ID].upd_load_uncached(gate_cost)

                    # Deleting cache if deallocation gate is sent
                    if isinstance(cmd.gate, DeallocateQubitGate):
                        self._print("Invalidating qubit ID")
                        dealloc_ID = cmd.qubits[0][0].id
                        try:
                            hist = self._cache.pop(dealloc_ID)
                        except KeyError:
                            raise DirtyQubitManagementError(
                                "A qubit which was not allocated was deallocated")
                    self.send([cmd])
        except:
            self.print_state(override=True)
            raise

        self.print_state()
        self._print("\n\n")

    def set_next_target(self, qubit):
        """
        Manually set the next target. The next dirty qubit that gets
        deallocated will be mapped into the provided qubit.

        Warning:
            No involvement-check is performed. If the dirty qubit
            interacts with the provided target, the remapping will change the
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

    def print_state(self, override=False):
        if not self._verbose and not override:
            return
        print("---------------------------------------------------------------")
        print("State of dirtymapper:")
        for qubit_id, qb_hist in self._cache.items():
            print("  Qubit " + str(qubit_id))
            print("    Sent on " + str(qb_hist.n_cmds_sent) + 
                  " cmds for a load of " + str(qb_hist.load_uncached) + ".")
            print("    Cached Commands:")
            for cmd, load in zip(qb_hist.cmds, qb_hist.loads):
                print("      " + str(cmd) + "  for " + str(load))
        for i, h in self._cache.items():
            h.print_state(i)
        print("--------------------------------------------------------------")


class QubitHistory:
    """
    Small class to make handling of data cached for a qubit easier
    """
    def __init__(self):
        # saves cmds
        self.cmds = list()
        # saves load on qb after corresponding cmd has been executed
        self.loads = list()
        # absolute pos = list_index + n_cmds_sent : absolute pos in circuit
        # saves absolute pos of this cmd in other qubits' cache
        # order corresponds to cmd.all_qubits
        self.abs_positions = list()
        # saves ids of other qubits acted on by the corresponding cmd
        # order corresponds to cmd.all_qubits
        self.inv_ids = list()
        # number of cmds sent on and not cached anymore
        self.n_cmds_sent = 0
        # Load on qubit after the last sent out cmd
        self.load_uncached = 0

    def check_invariants(self, ID=None):
        try:
            assert len(self.cmds) == len(self.loads), (
                "# of commands cached doesn't fit # of loads")
            assert len(self.cmds) == len(self.abs_positions), (
                "# of commands cached doesn't fit # of abs_positions-lists")
            assert len(self.cmds) == len(self.inv_ids), (
                "# of commands cached doesn't fit # of involved_id-lists")

            for cmd, pos_list in zip(self.cmds, self.abs_positions):
                assert (len(cmd.all_qubits[0]) +
                        len(cmd.all_qubits[1]) ==
                        len(pos_list)), (
                    "# of qubits involved in cmd doesn't fit # entries in " +
                    "abs_positions-list")

            for cmd, inv_list in zip(self.cmds, self.inv_ids):
                assert (len(cmd.all_qubits[0]) +
                        len(cmd.all_qubits[1]) ==
                        len(inv_list)), (
                    "# of qubits involved in cmd doesn't fit # entries in " +
                    "inv_ids-list")
            for i in range(len(self.loads)-1):
                assert self.loads[i] <= self.loads[i+1], (
                    "Load has to be increasing")
        except AssertionError:
            print("ERROR!")
            if not ID is None:
                print("ID: " + str(ID))
            self.print_state(ID)
            raise

    def sent_cmd(self):
        """
        Function to call when the first cmd in self.cmds is sent on.
        Cleans up the other saved information and updates self.n_cmds_sent and
        self.load_uncached.
        Returns the sent command
        """
        self.load_uncached = self.loads[0]
        self.n_cmds_sent += 1

        del self.inv_ids[0]
        del self.abs_positions[0]
        del self.loads[0]

        return self.cmds.pop(0)

    def cmd_at_abs(self, abs_pos):
        """
        Returns the command at absolute (position in circuit, not in cache)
        position.

            Args:
                abs_pos (int)   : Position of the command in the whole sequence
                                  of operations on the Qubit (includes uncached
                                  cmds)
        """
        return self.cmds[abs_pos - self.n_cmds_sent]

    def load_at_abs(self, abs_pos):
        """
        Returns the load at absolute (position in circuit, not in cache)
        position.

            Args:
                abs_pos (int)   : Position of the command in the whole sequence
                                  of operations on the Qubit (includes uncached
                                  cmds)
        """
        try:
            return self.loads[abs_pos - self.n_cmds_sent]
        except:
            print(abs_pos)
            print(self.n_cmds_sent)
            print(abs_pos - self.n_cmds_sent)
            print(len(self.loads))
            raise

    def load_now(self):
        """
        Returns the load on the qubit now. Returns the last entry in
        self.loads if there are cmds cached, if not then self.load_uncached 
        """
        if not self.loads:
            return self.load_uncached
        return self.loads[-1]

    def set_load_at_abs(self, abs_pos, load):
        """
        Sets the load at absolute (position in circuit, not in cache)
        position pos.

            Args:
                abs_pos (int)   : Position of the command in the whole sequence
                                  of operations on the Qubit (includes uncached
                                  cmds)
                load (int)      : The load after the cmd at abs_pos will be set
                                  to this value
        """
        self.loads[abs_pos - self.n_cmds_sent] = load

    def upd_load_uncached(self, cost):
        """
        Updates the cache when a cmd with cost acted on the qubit but wasn't
        cached

            Args:
                cost (int)  : Cost of the gate that was sent on uncached
        """
        self.n_cmds_sent += 1
        self.load_uncached += cost

    def add(self, cmd, ID_list, abs_positions, load):
        """
        Adds the command cmd to the cache and updates all relevant data
        (invariants are preserved)
            Args:
                cmd (Command)               : cmd to add to the cache
                ID_list (list<int>)         : List of IDs of the Qubits the cmd
                                              acts on. Is equal to the IDs of
                                              cmd.all_qubits (also in ordering
                                              of the qubits!), but is taken as
                                              argument for performance reasons.
                abs_positions (list<int>)   : List of the absolute positions of
                                              the cmd on other qubits. Has to
                                              correspond to ID_list
                cost (int)                  : Load on the qubit after executing
                                              cmd
        """
        self.cmds.append(cmd)
        self.loads.append(load)
        self.abs_positions.append(abs_positions)
        self.inv_ids.append(ID_list)

    def print_state(self, ID = None):
        print("######################################")
        print("Cache of qubit " + str(ID))
        print("Sent: " + str(self.n_cmds_sent))
        print("cmds: " + str(len(self.cmds)))
        print("loads: " + str(self.loads))
        print("inv_ids: " + str(self.inv_ids))
        print("pos: " + str(self.abs_positions))

        print("######################################")
            


def main():
    a = QubitHistory()
    a.check_invariants()

if __name__=='__main__':
    main()
