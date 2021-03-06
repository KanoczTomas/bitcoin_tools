import plyvel
from binascii import hexlify, unhexlify
from json import dumps
from math import ceil
from copy import deepcopy
from json import loads
from bitcoin_tools.analysis.leveldb import *
from bitcoin_tools.utils import change_endianness, txout_decompress


def b128_encode(n):
    """ Performs the MSB base-128 encoding of a given value. Used to store variable integers (varints) in the LevelDB.
    The code is a port from the Bitcoin Core C++ source. Notice that the code is not exactly the same since the original
    one reads directly from the LevelDB.

    The encoding is used to store Satoshi amounts into the Bitcoin LevelDB (chainstate). Before encoding, values are
    compressed using txout_compress.

    The encoding can also be used to encode block height values into the format use in the LevelDB, however, those are
    encoded not compressed.

    Explanation can be found in:
        https://github.com/bitcoin/bitcoin/blob/v0.13.2/src/serialize.h#L307L329
    And code:
        https://github.com/bitcoin/bitcoin/blob/v0.13.2/src/serialize.h#L343#L358

    The MSB of every byte (x)xxx xxxx encodes whether there is another byte following or not. Hence, all MSB are set to
    one except from the very last. Moreover, one is subtracted from all but the last digit in order to ensure a
    one-to-one encoding. Hence, in order decode a value, the MSB is changed from 1 to 0, and 1 is added to the resulting
    value. Then, the value is multiplied to the respective 128 power and added to the rest.

    Examples:

        - 255 = 807F (0x80 0x7F) --> (1)000 0000 0111 1111 --> 0000 0001 0111 1111 --> 1 * 128 + 127 = 255
        - 4294967296 (2^32) = 8EFEFEFF (0x8E 0xFE 0xFE 0xFF 0x00) --> (1)000 1110 (1)111 1110 (1)111 1110 (1)111 1111
            0000 0000 --> 0000 1111 0111 1111 0111 1111 1000 0000 0000 0000 --> 15 * 128^4 + 127*128^3 + 127*128^2 +
            128*128 + 0 = 2^32


    :param n: Value to be encoded.
    :type n: int
    :return: The base-128 encoded value
    :rtype: hex str
    """

    l = 0
    tmp = []
    data = ""

    while True:
        tmp.append(n & 0x7F)
        if l != 0:
            tmp[l] |= 0x80
        if n <= 0x7F:
            break
        n = (n >> 7) - 1
        l += 1

    tmp.reverse()
    for i in tmp:
        data += format(i, '02x')
    return data


def b128_decode(data):
    """ Performs the MSB base-128 decoding of a given value. Used to decode variable integers (varints) from the LevelDB.
    The code is a port from the Bitcoin Core C++ source. Notice that the code is not exactly the same since the original
    one reads directly from the LevelDB.

    The decoding is used to decode Satoshi amounts stored in the Bitcoin LevelDB (chainstate). After decoding, values
    are decompressed using txout_decompress.

    The decoding can be also used to decode block height values stored in the LevelDB. In his case, values are not
    compressed.

    Original code can be found in:
        https://github.com/bitcoin/bitcoin/blob/v0.13.2/src/serialize.h#L360#L372

    Examples and further explanation can be found in b128_encode function.

    :param data: The base-128 encoded value to be decoded.
    :type data: hex str
    :return: The decoded value
    :rtype: int
    """

    n = 0
    i = 0
    while True:
        d = int(data[2 * i:2 * i + 2], 16)
        n = n << 7 | d & 0x7F
        if d & 0x80:
            n += 1
            i += 1
        else:
            return n


def parse_b128(utxo, offset=0):
    """ Parses a given serialized UTXO to extract a base-128 varint.

    :param utxo: Serialized UTXO from which the varint will be parsed.
    :type utxo: hex str
    :param offset: Offset where the beginning of the varint if located in the UTXO.
    :type offset: int
    :return: The extracted varint, and the offset of the byte located right after it.
    :rtype: hex str, int
    """

    data = utxo[offset:offset+2]
    offset += 2
    more_bytes = int(data, 16) & 0x80  # MSB b128 Varints have set the bit 128 for every byte but the last one,
    # indicating that there is an additional byte following the one being analyzed. If bit 128 of the byte being read is
    # not set, we are analyzing the last byte, otherwise, we should continue reading.
    while more_bytes:
        data += utxo[offset:offset+2]
        more_bytes = int(utxo[offset:offset+2], 16) & 0x80
        offset += 2

    return data, offset


def decode_utxo(utxo):
    """ Decodes a LevelDB serialized UTXO. The serialized format is defined in the Bitcoin Core source as follows:
     Serialized format:
     - VARINT(nVersion)
     - VARINT(nCode)
     - unspentness bitvector, for vout[2] and further; least significant byte first
     - the non-spent CTxOuts (via CTxOutCompressor)
     - VARINT(nHeight)

     The nCode value consists of:
     - bit 1: IsCoinBase()
     - bit 2: vout[0] is not spent
     - bit 4: vout[1] is not spent
     - The higher bits encode N, the number of non-zero bytes in the following bitvector.
        - In case both bit 2 and bit 4 are unset, they encode N-1, as there must be at
        least one non-spent output).

    VARINT refers to the CVarint used along the Bitcoin Core client, that is base128 encoding. A CTxOut contains the
    compressed amount of Satoshis that the UTXO holds. That amount is encoded using the equivalent to txout_compress +
    b128_encode.

    :param utxo: UTXO to be decoded (extracted from the chainstate)
    :type utxo: hex str
    :return; The decoded UTXO.
    :rtype: dict
    """

    # Version is extracted from the first varint of the serialized utxo
    version, offset = parse_b128(utxo)
    version = b128_decode(version)

    # The next MSB base 128 varint is parsed to extract both is the utxo is coin base (first bit) and which of the
    # outputs are not spent.
    code, offset = parse_b128(utxo, offset)
    code = b128_decode(code)
    coinbase = code & 0x01

    # Check if the first two outputs are spent
    vout = [(code | 0x01) & 0x02, (code | 0x01) & 0x04]

    # The higher bits of the current byte (from the fourth onwards) encode n, the number of non-zero bytes of
    # the following bitvector. If both vout[0] and vout[1] are spent (v[0] = v[1] = 0) then the higher bits encodes n-1,
    # since there should be at least one non-spent output.
    if not vout[0] and not vout[1]:
        n = (code >> 3) + 1
        vout = []
    else:
        n = code >> 3
        vout = [i for i in xrange(len(vout)) if vout[i] is not 0]

    # If n is set, the encoded value contains a bitvector. The following bytes are parsed until n non-zero bytes have
    # been extracted. (If a 00 is found, the parsing continues but n is not decreased)
    if n > 0:
        bitvector = ""
        while n:
            data = utxo[offset:offset+2]
            if data != "00":
                n -= 1
            bitvector += data
            offset += 2

        # Once the value is parsed, the endianness of the value is switched from LE to BE and the binary representation
        # of the value is checked to identify the non-spent output indexes.
        bin_data = format(int(change_endianness(bitvector), 16), '0'+str(n*8)+'b')[::-1]

        # Every position (i) with a 1 encodes the index of a non-spent output as i+2, since the two first outs (v[0] and
        # v[1] has been already counted)
        # (e.g: 0440 (LE) = 4004 (BE) = 0100 0000 0000 0100. It encodes outs 4 (i+2 = 2+2) and 16 (i+2 = 14+2).
        extended_vout = [i+2 for i in xrange(len(bin_data))
                         if bin_data.find('1', i) == i]  # Finds the index of '1's and adds 2.

        # Finally, the first two vouts are included to the list (if they are non-spent).
        vout += extended_vout

    # Once the number of outs and their index is known, they could be parsed.
    outs = []
    for i in vout:
        # The Satoshis amount is parsed, decoded and decompressed.
        data, offset = parse_b128(utxo, offset)
        amount = txout_decompress(b128_decode(data))
        # The output type is also parsed.
        out_type, offset = parse_b128(utxo, offset)
        out_type = b128_decode(out_type)
        # Depending on the type, the length of the following data will differ.  Types 0 and 1 refers to P2PKH and P2SH
        # encoded outputs. They are always followed 20 bytes of data, corresponding to the hash160 of the address (in
        # P2PKH outputs) or to the scriptHash (in P2PKH). Notice that the leading and tailing opcodes are not included.
        # If 2-5 is found, the following bytes encode a public key. The first byte in this case should be also included,
        # since it determines the format of the key.
        if out_type in [0, 1]:
            data_size = 40  # 20 bytes
        elif out_type in [2, 3, 4, 5]:
            data_size = 66  # 33 bytes (1 byte for the type + 32 bytes of data)
            offset -= 2
        # Finally, if another value is found, it represents the length of the following data, which is uncompressed.
        else:
            data_size = (out_type - NSPECIALSCRIPTS) * 2  # If the data is not compacted, the out_type corresponds
            # to the data size adding the number os special scripts (nSpecialScripts).

        # And finally the address (the hash160 of the public key actually)
        data, offset = utxo[offset:offset+data_size], offset + data_size
        outs.append({'index': i, 'amount': amount, 'out_type': out_type, 'data': data})

    # Once all the outs are processed, the block height is parsed
    height, offset = parse_b128(utxo, offset)
    height = b128_decode(height)
    # And the length of the serialized utxo is compared with the offset to ensure that no data remains unchecked.
    assert len(utxo) == offset

    return {'version': version, 'coinbase': coinbase, 'outs': outs, 'height': height}


def display_decoded_utxo(decoded_utxo):
    """ Displays the information extracted from a decoded UTXO from the chainstate.

    :param decoded_utxo: Decoded UTXO from the chainstate
    :type decoded_utxo: dict
    :return: None
    :rtype: None
    """

    print "version: " + str(decoded_utxo['version'])
    print "isCoinbase: " + str(decoded_utxo['coinbase'])

    outs = decoded_utxo['outs']
    print "Number of outputs: " + str(len(outs))
    for out in outs:
        print "vout[" + str(out['index']) + "]:"
        print "\tSatoshi amount: " + str(out['amount'])
        print "\tOutput code type: " + out['out_type']
        print "\tHash160 (Address): " + out['address']

    print "Block height: " + str(decoded_utxo['height'])


def parse_ldb(fout_name):
    """
    Parsed data from the chainstate LevelDB and stores it in a output file.
    :param fout_name: Name of the file to output the data.
    :type fout_name: str
    :return: None
    :rtype: None
    """

    # Output file
    fout = open(CFG.data_path + fout_name, 'w')
    # Open the LevelDB
    db = plyvel.DB(CFG.btc_core_path + "/chainstate", compression=None)  # Change with path to chainstate

    # Load obfuscation key (if it exists)
    o_key = db.get((unhexlify("0e00") + "obfuscate_key"))

    # If the key exists, the leading byte indicates the length of the key (8 byte by default). If there is no key,
    # 8-byte zeros are used (since the key will be XORed with the given values).
    if o_key is not None:
        o_key = hexlify(o_key)[2:]
    else:
        o_key = "0000000000000000"

    # For every UTXO (identified with a leading 'c'), the key (tx_id) and the value (encoded utxo) is displayed.
    # UTXOs are obfuscated using the obfuscation key (o_key), in order to get them non-obfuscated, a XOR between the
    # value and the key (concatenated until the length of the value is reached) if performed).
    for key, o_value in db.iterator(prefix=b'c'):
        value = "".join([format(int(v, 16) ^ int(o_key[i % len(o_key)], 16), 'x') for i, v in enumerate(hexlify(o_value))])
        assert len(hexlify(o_value)) == len(value)
        fout.write(dumps({"key":  hexlify(key), "value": value}) + "\n")

    db.close()


def accumulate_dust_lm(fin_name, fout_name="dust.txt"):
    """
    Accumulates all the dust / lm of a given parsed utxo file (from utxo_dump function).

    :param fin_name: Input file name, from where data wil be loaded.
    :type fin_name: str
    :param fout_name: Output file name, where data will be stored.
    :type fout_name: str
    :return: None
    :rtype: None
    """

    # Dust calculation
    # Input file
    fin = open(CFG.data_path + fin_name, 'r')

    dust = {str(fee_per_byte): 0 for fee_per_byte in range(MIN_FEE_PER_BYTE, MAX_FEE_PER_BYTE, FEE_STEP)}
    value_dust = deepcopy(dust)
    data_len_dust = deepcopy(dust)

    lm = deepcopy(dust)
    value_lm = deepcopy(dust)
    data_len_lm = deepcopy(dust)

    total_utxo = 0
    total_value = 0
    total_data_len = 0

    for line in fin:
        data = loads(line[:-1])

        for fee_per_byte in range(MIN_FEE_PER_BYTE, MAX_FEE_PER_BYTE, FEE_STEP):
            if fee_per_byte >= data["dust"] != 0:
                dust[str(fee_per_byte)] += 1
                value_dust[str(fee_per_byte)] += data["amount"]
                data_len_dust[str(fee_per_byte)] += data["utxo_data_len"]
            if fee_per_byte >= data["loss_making"] != 0:
                lm[str(fee_per_byte)] += 1
                value_lm[str(fee_per_byte)] += data["amount"]
                data_len_lm[str(fee_per_byte)] += data["utxo_data_len"]

        total_utxo = total_utxo + 1
        total_value += data["amount"]
        total_data_len += data["utxo_data_len"]

    fin.close()

    data = {"dust_utxos": dust, "dust_value": value_dust, "dust_data_len": data_len_dust,
            "lm_utxos": lm, "lm_value": value_lm, "lm_data_len": data_len_lm,
            "total_utxos": total_utxo, "total_value": total_value, "total_data_len": total_data_len}

    # Store dust calculation in a file.
    out = open(CFG.data_path + fout_name, 'w')
    out.write(dumps(data))
    out.close()


def check_multisig(script, std=True):
    """
    Checks whether a given script is a multisig one. By default, only standard multisig script are accepted.

    :param script: The script to be checked.
    :type script: str
    :param std: Whether the script is standard or not.
    :type std: bool
    :return: True if the script is multisig (under the std restrictions), False otherwise.
    :rtype: bool
    """

    if std:
        # Standard bare Pay-to-multisig only accepts up to 3-3.
        r = range(81, 83)
    else:
        # m-of-n combination is valid up to 20.
        r = range(84, 101)

    if int(script[:2], 16) in r and script[2:4] in ["21", "41"] and script[-2:] == "ae":
        return True
    else:
        return False


def get_min_input_size(out, height, count_p2sh=False):
    """
    Computes the minimum size an input created by a given output type (parsed from the chainstate) will have.
    The size is computed in two parts, a fixed size that is non type dependant, and a variable size which
    depends on the output type.

    :param out: Output type.
    :type out: int
    :param height: Block height where the utxo was created. Used to set P2PKH min_size.
    :type height: int
    :param count_p2sh: Whether P2SH should be taken into account.
    :type count_p2sh: bool
    :return: The minimum input size of the given output type.
    :rtype: int
    """

    out_type = out["out_type"]
    script = out["data"]

    # Fixed size
    prev_tx_id = 32
    prev_out_index = 4
    nSequence = 4

    fixed_size = prev_tx_id + prev_out_index + nSequence

    # Variable size (depending on scripSig):
    # Public key size can be either 33 or 65 bytes, depending on whether the key is compressed or uncompressed. We wil
    # make them fall in one of the categories depending on the block height in which the transaction was included.
    #
    # Signatures size is contained between 71-73 bytes depending on the size of the S and R components of the signature.
    # Since we are looking for the minimum size, we will consider all signatures to be 71-byte long in order to define
    # a lower bound.

    if out_type is 0:
        # P2PKH
        # Bitcoin core starts using compressed pk in version (0.6.0, 30/03/12, around block height 173480)
        if height < 173480:
            # uncompressed keys
            scriptSig = 138  # PUSH sig (1 byte) + sig (71 bytes) + PUSH pk (1 byte) + uncompressed pk (65 bytes)
        else:
            # compressed keys
            scriptSig = 106  # PUSH sig (1 byte) + sig (71 bytes) + PUSH pk (1 byte) + compressed pk (33 bytes)
        scriptSig_len = 1
    elif out_type is 1:
        # P2SH
        # P2SH inputs can have arbitrary length. Defining the length of the original script by just knowing the hash
        # is infeasible. Two approaches can be followed in this case. The first one consists on considering P2SH
        # by defining the minimum length a script of such type could have. The other approach will be ignoring such
        # scripts when performing the dust calculation.
        if count_p2sh:
            # If P2SH UTXOs are considered, the minimum script that can be created has only 1 byte (OP_1 for example)
            scriptSig = 1
            scriptSig_len = 1
        else:
            # Otherwise, we will define the length as 0 and skip such scripts for dust calculation.
            scriptSig = -fixed_size
            scriptSig_len = 0
    elif out_type in [2, 3, 4, 5]:
        # P2PK
        # P2PK requires a signature and a push OP_CODE to push the signature into the stack. The format of the public
        # key (compressed or uncompressed) does not affect the length of the signature.
        scriptSig = 72  # PUSH sig (1 byte) + sig (71 bytes)
        scriptSig_len = 1
    else:
        # P2MS
        if check_multisig(script):
            # Multisig can be 15-15 at most.
            req_sigs = int(script[:2], 16) - 80  # OP_1 is hex 81
            scriptSig = 1 + (req_sigs * 72)  # OP_0 (1 byte) + 72 bytes per sig (PUSH sig (1 byte) + sig (71 bytes))
            scriptSig_len = int(ceil(scriptSig / float(256)))
        else:
            # All other types (non-standard outs)
            scriptSig = -fixed_size - 1  # Those scripts are marked with length -1 and skipped in dust calculation.
            scriptSig_len = 0

    var_size = scriptSig_len + scriptSig

    return fixed_size + var_size
