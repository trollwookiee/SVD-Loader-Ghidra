# Load specified SVD and generate peripheral memory maps & structures.
#@author Thomas Roth thomas.roth@leveldown.de
#@category leveldown security
#@keybinding
#@menupath
#@toolbar

# More information:
# https://leveldown.de/blog/svd-loader/
# License: GPLv3

from cmsis_svd.parser import SVDParser
from ghidra.program.model.data import Structure, StructureDataType, UnsignedIntegerDataType, DataTypeConflictHandler
from ghidra.program.model.data import UnsignedShortDataType, ByteDataType, UnsignedLongLongDataType
from ghidra.program.model.mem import MemoryBlockType
from ghidra.program.model.address import AddressFactory
from ghidra.program.model.symbol import SourceType
from ghidra.program.model.mem import MemoryConflictException


class MemoryRegion:

    def __init__(self, name, start, end):
        self.name = name
        self.start = start
        self.end = end

    def length(self):
        return self.end - self.start


def reduce_memory_regions(regions):
    for i in range(len(regions)):
        r1 = regions[i]
        for j in range(len(regions)):
            r2 = regions[j]
            # Skip self
            if i == j:
                continue
            if r1.end < r2.start:
                continue
            if r2.end < r1.start:
                continue

            # We are overlapping, generate larger area and call
            # reduce_memory_regions again.
            regions[i].start = min(r1.start, r2.start)
            regions[i].end = max(r1.end, r2.end)
            regions[i].name = r1.name + "_" + r2.name
            regions.remove(regions[j])
            return reduce_memory_regions(regions)
    return regions


def calculate_peripheral_size(peripheral, default_register_size):
    size = 0
    for register in peripheral.registers:
        register_size = default_register_size if not register._size else register._size
        size = max(size, register.address_offset + register_size / 8)
    return size


svd_file = askFile("Choose SVD file", "Load SVD File")

print("Loading SVD file...")
parser = SVDParser.for_xml_file(str(svd_file))
print("\tDone!")

# CM0, CM4, etc
cpu_type = parser.get_device().cpu.name
# little/big
cpu_endian = parser.get_device().cpu.endian

default_register_size = parser.get_device().size

# Not all SVDs contain these fields
if cpu_type and not cpu_type.startswith("CM"):
    print("Currently only Cortex-M CPUs are supported.")
    print("Supplied CPU type was: " + cpu_type)
    sys.exit(1)

if cpu_endian and cpu_endian != "little":
    print("Currently only little endian CPUs are supported.")
    print("Supplied CPU endian was: " + cpu_endian)
    sys.exit(1)

# Get things we need
listing = currentProgram.getListing()
symtbl = currentProgram.getSymbolTable()
dtm = currentProgram.getDataTypeManager()
space = currentProgram.getAddressFactory().getDefaultAddressSpace()

namespace = symtbl.getNamespace("Peripherals", None)
if not namespace:
    namespace = currentProgram.getSymbolTable().createNameSpace(None, "Peripherals", SourceType.ANALYSIS)

peripherals = parser.get_device().peripherals

print("Generating memory regions...")
# First, we need to generate a list of memory regions.
# This is because some SVD files have overlapping peripherals...
memory_regions = []
for peripheral in peripherals:
    print("peripheral {}".format(peripheral.name))

    # Skip _INTERRUPTS
    if peripheral.name == '_INTERRUPTS':
        print("\tSkip peripheral {}".format(peripheral.name))
        continue

    start = peripheral.base_address

    if(peripheral.address_block):
        peripheral_length = peripheral.address_block.offset + peripheral.address_block.size
        end = peripheral.base_address + peripheral_length
        print("\t{} addressBlock: {}-{}: {} bytes".format(peripheral.name, hex(start),  hex(end), peripheral_length))

    elif(len(peripheral.registers) > 0):
        peripheral_length = 0
        for register in peripheral.registers:
            register_start = peripheral.base_address + register.address_offset
            register_size = default_register_size if not register._size else register._size
            register_length = register_size / 8  # bits to bytes
            peripheral_length += register_length  # bytes
            register_end = register_start + register_length
            print("\t{}: {}-{}: {} bytes".format(register.name,  hex(register_start),  hex(register_end), register_length))

        end = peripheral.base_address + peripheral_length

    memory_regions.append(MemoryRegion(peripheral.name, start, end))
memory_regions = reduce_memory_regions(memory_regions)

# Create memory blocks:
print("Create memory blocks:...")
for r in memory_regions:
    try:
        addr = space.getAddress(r.start)
        length = r.length()

        print("\tcreateUninitializedBlock: {}: {} {} bytes".format(r.name,  addr,  length))

        t = currentProgram.memory.createUninitializedBlock(r.name, addr, length, False)
        t.setRead(True)
        t.setWrite(True)
        t.setExecute(False)
        t.setVolatile(True)
        t.setComment("Generated by SVD-Loader.")
    except Exception as e:
        print("\tFailed to generate memory block for: " + r.name)
        print("\t", e)

print("\tDone!")

print("Generating peripherals...")
for peripheral in peripherals:
    print("\t" + peripheral.name)

    if(len(peripheral.registers) == 0):
        print("\t\tNo registers.")
        continue

    if(not peripheral.address_block):
        print("\t\tNo addressBlock.")
        continue

    # try:
    # Iterage registers to get size of peripheral
    # Most SVDs have an address-block that specifies the size, but
    # they are often far too large, leading to issues with overlaps.
    length = calculate_peripheral_size(peripheral, default_register_size)

    # Generate structure for the peripheral
    peripheral_struct = StructureDataType(peripheral.name, length)

    peripheral_start = peripheral.base_address
    peripheral_end = peripheral_start + length

    for register in peripheral.registers:
        register_size = default_register_size if not register._size else register._size

        r_type = UnsignedIntegerDataType()
        rs = register_size / 8
        if rs == 1:
            r_type = ByteDataType()
        elif rs == 2:
            r_type = UnsignedShortDataType()
        elif rs == 8:
            r_type = UnsignedLongLongDataType()

        peripheral_struct.replaceAtOffset(register.address_offset, r_type, register_size / 8, register.name, register.description)

    addr = space.getAddress(peripheral_start)

    dtm.addDataType(peripheral_struct, DataTypeConflictHandler.REPLACE_HANDLER)

    listing.createData(addr, peripheral_struct, False)

    symtbl.createLabel(addr,
                       peripheral.name,
                       namespace,
                       SourceType.USER_DEFINED)
    # except:
    #   print("\t\tFailed to generate peripheral " + peripheral.name)
