from collections import deque, OrderedDict
import traceback
import os
import time
from functools import partial
import threading

from .worker import Worker, InputPin, OutputPin
from .asynckernel import Transformer as KernelTransformer
from .asynckernel.remote.client import JobTransformer as ServiceTransformer
#TODO: multiple types of remote transformers

from .protocol import content_types

class Transformer(Worker):
    """
    This is the main-thread part of the transformer
    """
    transformer = None
    transformer_thread = None
    output_thread = None
    active = False
    _destroyed = False
    _listen_output_state = None

    def __init__(self, transformer_params, *, in_equilibrium=False, service=False):
        self.state = {}
        self.service = service
        self.code = InputPin(self, "code", "ref", "pythoncode", "transformer")
        #TODO: transfer_mode becomes "copy" when we switch from threads to processes
        thread_inputs = {"code": ("ref", "pythoncode", "transformer")}
        self._io_attrs = ["code"]
        self._pins = {"code":self.code}
        self._output_name = None
        self._last_value = None
        self._last_value_preliminary = False
        self._message_id = 0
        self._transformer_params = OrderedDict()
        self._in_equilibrium = in_equilibrium #transformer is initially in equilibrium
        forbidden = ("code",)
        for p in sorted(transformer_params.keys()):
            if p in forbidden:
                raise ValueError("Forbidden pin name: %s" % p)
            param = transformer_params[p]
            self._transformer_params[p] = param
            pin = None
            io, transfer_mode, access_mode, content_type = None, "ref", None, None
            #TODO: change "ref" to "copy" once transport protocol works
            if isinstance(param, str):
                io = param
            elif isinstance(param, (list, tuple)):
                io = param[0]
                if len(param) > 1:
                    transfer_mode = param[1]
                if len(param) > 2:
                    access_mode = param[2]
                if len(param) > 3:
                    content_type = param[3]
            elif isinstance(param, dict):
                io = param["io"]
                transfer_mode = param.get("transfer_mode", transfer_mode)
                access_mode = param.get("access_mode", access_mode)
                content_type = param.get("content_type", content_type)
            else:
                raise ValueError((p, param))
            if content_type is None and access_mode in content_types:
                content_type = access_mode
            if io == "input":
                pin = InputPin(self, p, transfer_mode, access_mode)
                thread_inputs[p] = transfer_mode, access_mode, content_type
            elif io == "output":
                pin = OutputPin(self, p, transfer_mode, access_mode)
                assert self._output_name is None  # can have only one output
                self._output_name = p
            else:
                raise ValueError(io)

            if pin is not None:
                self._io_attrs.append(p)
                self._pins[p] = pin

        """Output listener thread
        - It must have the same memory space as the main thread
        - It must run async from the main thread
        => This will always be a thread, regardless of implementation
        """
        self.output_finish = threading.Event()
        self.output_queue = deque()
        self.output_semaphore = threading.Semaphore(0)

        """Transformer thread
        For now, it is implemented as a thread
         However, it could as well be implemented as process
        - It shares no memory space with the main thread
          (other than the deques and semaphores, which could as well be
           implemented using network sockets)
        - It must run async from the main thread
        """

        KernelTransformerClass = ServiceTransformer if service else KernelTransformer
        self.transformer = KernelTransformerClass(
            self,
            thread_inputs, self._output_name,
            self.output_queue, self.output_semaphore,
            in_equilibrium = self._in_equilibrium
        )
        self._in_equilibrium = False
        super().__init__()

    def __str__(self):
        ret = "Seamless transformer: " + self._format_path()
        return ret

    def activate(self, only_macros):
        super().activate(only_macros)
        if self.active:
            return

        thread = threading.Thread(target=self.listen_output, daemon=True) #TODO: name
        self.output_thread = thread
        self.output_thread.start()

        if self.transformer_thread is None:
            thread = threading.Thread(target=self.transformer.run, daemon=True) #TODO: name
            self.transformer_thread = thread
            self.transformer_thread.start()

        self.active = True

    def _send_message(self, msg):
        self._message_id += 1
        self._pending_updates += 1
        labeled_msg = (self._message_id,) + msg
        self.transformer.input_queue.append(labeled_msg)
        self.transformer.semaphore.release()

    def receive_update(self, input_pin, value, checksum, access_mode, content_type):
        if not self.active:
            work = partial(self.receive_update, input_pin, value, checksum, access_mode, content_type)
            self._get_manager().buffered_work.append(work)
            return
        if checksum is None and value is not None:
            checksum = str(value) #KLUDGE; as long as structured_cell doesn't compute checksums...
        if not self._receive_update_checksum(input_pin, checksum):
            return
        self._send_message( (input_pin, value, access_mode, content_type) )

    def _touch(self):
        self._send_message( ("@TOUCH", None, None, None) )

    @property
    def debug(self):
        return self.transformer.debug

    @debug.setter
    def debug(self, value):
        assert isinstance(value, bool), value
        old_value = self.transformer.debug
        if value != old_value:
            self.transformer.debug = value
            manager = self._get_manager()
            manager.touch_worker(self)

    def listen_output(self):
        # TODO logging
        # TODO requires_function cleanup

        # This code is very convoluted... networking expert wanted for cleanup!

        def get_item():
            self.output_semaphore.acquire()
            if self.output_finish.is_set():
                if not self.output_queue:
                    return
            output_name, output_value = self.output_queue.popleft()
            return output_name, output_value

        def receive_end():
            nonlocal updates_on_hold
            if updates_on_hold:
                for n in range(100): #100x5 ms
                    ok = self.output_semaphore.acquire(blocking=False)
                    if ok:
                        self.output_semaphore.release()
                        break
                    time.sleep(0.005)
                else:
                    # should only happen if killed
                    self._pending_updates -= updates_on_hold
                    updates_on_hold = 0

        if self._listen_output_state is None:
            updates_on_hold = 0
            between_start_end = False
        else:
            updates_on_hold, between_start_end = self._listen_output_state
            self._listen_output_state = None

        while True:
            try:
                output_name, output_value = None, None
                if updates_on_hold:
                    """
                    Difficult situation. At the one hand, we can't hold on to
                    these processed updates forever:
                     It would keep the transformer marked as unstable, blocking
                      equilibrate().
                    On the other hand, an output_value could be just waiting
                    for us. If we decrement _pending_updates too early, this may
                    unblock equilibrate() while equilibrium has not been reached
                    The solution is that the kernel must respond within 500 ms
                    with an @START signal, and then a @END signal when the
                    computation is complete
                    """
                    for n in range(100): #100x5 ms
                        ok = self.output_semaphore.acquire(blocking=False)
                        if ok:
                            self.output_semaphore.release()
                            break
                        time.sleep(0.005)
                    else:
                        self._pending_updates -= updates_on_hold
                        updates_on_hold = 0

                if not between_start_end:
                    item = get_item()
                    if item is None:
                        break
                    if item[0] == "@RESTART":
                        self._listen_output_state = (updates_on_hold, between_start_end)
                        break
                    output_name, output_value = item
                    if output_name == "@START":
                        between_start_end = True
                        item = get_item()
                        if item is None:
                            break
                        if item[0] == "@RESTART":
                            self._listen_output_state = (updates_on_hold, between_start_end)
                            break
                        output_name, output_value = item
                        assert output_name in ("@PRELIMINARY", "@END"), output_name
                        if output_name == "@END":
                            between_start_end = False
                            receive_end()
                            item = get_item()
                            if item is None:
                                break
                            if item[0] == "@RESTART":
                                self._listen_output_state = (updates_on_hold, between_start_end)
                                break
                            output_name, output_value = item

                if output_name is None and output_value is not None:
                    updates_processed = output_value[0]
                    if self._pending_updates < updates_processed:
                        #This will not set the worker as stable
                        self._pending_updates -= updates_processed
                    else:
                        # hold on to updates_processed for a while, we don't
                        #  want to set the worker as stable before we have
                        #  done a send_update
                        updates_on_hold += updates_processed
                    continue

                preliminary = False
                if output_name == "@PRELIMINARY":
                    preliminary = True
                    output_name, output_value = item[1]
                elif between_start_end:
                    if output_name is None:
                        #TODO: this shouldn't happen...
                        item = get_item()
                        between_start_end = False
                        receive_end()
                        continue
                    assert output_name == "@END", output_name
                    between_start_end = False
                    receive_end()
                    continue
                    ###item = get_item()
                    ###if item is None:
                    ###    break
                    ###output_name, output_value = item

                if output_name == "@ERROR":
                    pass #TODO: log error
                else:
                    assert output_name == self._output_name, item
                    if self._output_name is not None:
                        pin = self._pins[self._output_name]
                        #we're not in the main thread, but the manager takes care of it
                        pin.send_update(output_value, preliminary=preliminary)

                    if preliminary:
                        continue

                item = get_item()
                if item is None:
                    break
                if item[0] == "@RESTART":
                    self._listen_output_state = (updates_on_hold, between_start_end)
                    break
                output_name, output_value = item
                assert output_name is None
                updates_processed = output_value[0]
                self._pending_updates -= updates_processed

                if updates_on_hold:
                    self._pending_updates -= updates_on_hold
                    updates_on_hold = 0
            except Exception:
                traceback.print_exc() #TODO: store it?

    def destroy(self, from_del=False):
        if not self.active:
            return
        if self._destroyed:
            return

        # gracefully terminate the transformer thread
        if self.transformer_thread is not None:
            self.transformer.finish.set()
            self.transformer.semaphore.release() # to unblock the .finish event
            # TODO: after some caching events, transformer threads lock up at destroy()
            # to prevent this, release just once more (kludge)
            self.transformer.semaphore.release()
            if not from_del:
                pass
                # disable for now, since it is so slow... may break macros
                #self.transformer.finished.wait()
                #self.transformer_thread.join()
            del self.transformer_thread
            self.transformer_thread = None

        # gracefully terminate the output thread
        if self.output_thread is not None:
            self.output_finish.set()
            self.output_semaphore.release() # to unblock for the output_finish
            if not from_del:
                self.output_thread.join()
            del self.output_thread
            self.output_thread = None
        if not from_del:
            self._pending_updates = 0

    def full_destroy(self,from_del=False):
        self.self.destroy(from_del=from_del)

    def __dir__(self):
        return object.__dir__(self) + list(self._pins.keys())


    def status(self):
        """The computation status of the transformer
        Returns a dictionary containing the status of all pins that are not OK.
        If all pins are OK, returns the status of the transformer itself: OK or pending
        """
        if self._in_equilibrium or \
          (self.transformer is not None and self.transformer.in_equilibrium):
            return self.StatusFlags.OK.name
        result = {}
        for pinname, pin in self._pins.items():
            s = pin.status()
            if s != self.StatusFlags.OK.name:
                result[pinname] = s
        t = self.transformer
        for pinname in t._pending_inputs:
            if pinname not in result:
                result[pinname] = self.StatusFlags.PENDING.name
        if len(result):
            return result
        if t._pending_updates:
            return self.StatusFlags.PENDING.name
        if self.transformer.exception is not None:
            return self.StatusFlags.ERROR.name
        return self.StatusFlags.OK.name

def transformer(params, in_equilibrium=False, service=False):
    return Transformer(
       params,
       in_equilibrium=in_equilibrium,
       service=service
    )
