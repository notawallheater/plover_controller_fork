import sys
import logging
import pdb

#pdb.set_trace()

#logging.basicConfig(level=logging.DEBUG)

def trace_function(frame, event, arg):
    if event == 'call':
        code = frame.f_code
        func_name = code.co_name
        file_name = code.co_filename
        line_no = frame.f_lineno
        logging.debug(f"Call: {func_name} in {file_name}:{line_no}")
    return trace_function

#sys.settrace(trace_function)
