#!/usr/bin/env python

# we're using python 3.x style print but want it to work in python 2.x,
from __future__ import print_function
import re, os, argparse, sys, math, warnings, subprocess
from collections import defaultdict

parser = argparse.ArgumentParser(description="Given a counts directory and a set of "
                                 "metaparameters, this script does the language model discounting "
                                 "and computes the objective function, which is the log-probability "
                                 "per word on the dev set.");


parser.add_argument("--derivs-out", type=str,
                    help="Filename to which to write derivatives (if supplied)");
parser.add_argument("count_dir",
                    help="Directory from which to obtain for counts files\n");
parser.add_argument("metaparameters",
                    help="Filename from which to read metaparameters");
parser.add_argument("objf_out",
                    help="Filename to which to write objective function");
parser.add_argument("work_dir",
                    help="Directory used to temporarily store files and for logs");



args = parser.parse_args()


# Add the script dir and the src dir to the path.
os.environ['PATH'] = (os.environ['PATH'] + os.pathsep +
                      os.path.abspath(os.path.dirname(sys.argv[0])) + ":" +
                      os.path.abspath(os.path.dirname(sys.argv[0])) +
                      os.pathsep + "src");

if not os.path.exists(args.work_dir):
    os.makedirs(args.work_dir)

if os.system("validate_count_dir.py " + args.count_dir) != 0:
    sys.exit(1)

# read the variables 'ngram_order', 'num_train_sets' and 'num_words'
# from the corresponding files in count_dir.
for name in [ 'ngram_order', 'num_train_sets', 'num_words' ]:
    f = open(args.count_dir + os.sep + name)
    globals()[name] = int(f.readline())
    f.close()

if os.system("validate_metaparameters.py --ngram-order={ngram_order} "
             "--num-train-sets={num_train_sets} {metaparameters}".format(
        ngram_order=ngram_order, num_train_sets=num_train_sets,
        metaparameters = args.metaparameters)) != 0:
    sys.exit(1)



# read the metaparameters as dicts.
# train_set_scale will be a map from integer
# training-set number to floating-point scale.  Note: there is no checking
# because we already called validate_metaparameters.py.
f = open(args.metaparameters, "r")
train_set_scale = {}
for n in range(1, num_train_sets + 1):
    train_set_scale[n] = float(f.readline().split()[1])
# the discounting constants will be stored as maps d1,d2,d3 from integer order
# to discounting constant.
d1 = {}
d2 = {}
d3 = {}
for o in range(2, ngram_order + 1):
    d1[o] = float(f.readline().split()[1])
    d2[o] = float(f.readline().split()[1])
    d3[o] = float(f.readline().split()[1])
f.close()


def RunCommand(command):
    # print the command for logging
    print(command, file=sys.stderr)
    if os.system(command) != 0:
        sys.exit("get_objf_and_derivs.py: error running command: " + command, file=sys.stderr)

def GetCommandStdout(command):
    # print the command for logging
    print(command, file=sys.stderr)
    try:
        output = subprocess.check_output(command, shell = True)
    except:
        sys.exit("get_objf_and_derivs.py: error running command: " + command)
    return output


# This function does the count merging for the specified
# n-gram order, writing to $work_dir/merged.$order
# For the highest order we merge count_dir/int.*.order,
# each with its appropriate scalign factor; for orders
# strictly between the highest order and 1 we merge those
# but also the discounted counts from work_dir/discounted.order;
# for order 1, no merging is done (-> this function shouldn't be
# called).
def MergeCounts(order):
    # merge counts of the specified order > 1.
    assert order > 1
    command = "merge-counts";
    for n in range(1, num_train_sets + 1):
        command += " {counts}/int.{train_set}.{order},{scale}".format(
            counts = args.count_dir, train_set = n, order = order,
            scale = train_set_scale[n])
    # for orders less than the highest order, we also have to include the
    # discounted counts from the one-higher order.  there is no scale here, so
    # the program will expect general-counts, not int-counts.
    if order < ngram_order:
        command += " {work}/discounted.{order}".format(
            work = args.work_dir, order = order)
    # the output gets redirected to the output file.
    command += " >{work}/merged.{order}".format(
        work = args.work_dir, order = order)
    RunCommand(command)

def MergeCountsBackward(order):
    global scale_derivs
    # merge counts of the specified order > 1; the backprop phase.
    assert order > 1
    command = "merge-counts-backward {work}/merged.{order} {work}/merged_derivs.{order} ".format(
        work = args.work_dir, order = order)

    for n in range(1, num_train_sets + 1):
        command += " {counts}/int.{train_set}.{order} {scale}".format(
            counts = args.count_dir, train_set = n, order = order,
            scale = train_set_scale[n])
    # for orders less than the highest order, we also have to include the
    # discounted counts from the one-higher order, and provide a filename
    # for it to output the derivatives w.r.t. that file.
    if order < ngram_order:
        command += " {work}/discounted.{order} {work}/discounted_derivs.{order}".format(
            work = args.work_dir, order = order)
    output = GetCommandStdout(command)
    try:
        this_scale_derivs = [ float(n) / num_dev_set_words for n in output.split() ]
        assert len(scale_derivs) == num_train_sets
        # the scaling factors are applied for each order > 1, and the
        # derivatives will be a sum over the derivatives for each of these
        # orders.
        for n in range(num_train_sets):
            scale_derivs[n] += this_scale_derivs[n]
    except:
        sys.exit("get_objf_and_derivs.py: unexpected output from command:" + output)


def DiscountCounts(order):
    # discount counts of the specified order > 1.
    assert order > 1
    command = "discount-counts {d1} {d2} {d3} {work}/merged.{order} {work}/float.{order} {work}/discounted.{orderm1} ".format(
        d1 = d1[order], d2 = d2[order], d3 = d3[order], work = args.work_dir,
        order = order, orderm1 = order - 1)
    RunCommand(command)

def DiscountCountsBackward(order):
    # discount counts of the specified order > 1; backprop version.
    assert order > 1
    command = ("discount-counts-backward {d1} {d2} {d3} {work}/merged.{order} {work}/float.{order} "
               "{work}/float_derivs.{order} {work}/discounted.{orderm1} {work}/discounted_derivs.{orderm1} "
               "{work}/merged_derivs.{order}".format(
            d1 = d1[order], d2 = d2[order], d3 = d3[order], work = args.work_dir,
            order = order, orderm1 = order - 1))
    output = GetCommandStdout(command);
    try:
        [ deriv1, deriv2, deriv3 ] = output.split()
    except:
        sys.exit("get_objf_and_derivs.py: could not parse output of command: " + output)
    d1_deriv[order] = float(deriv1) / num_dev_set_words
    d2_deriv[order] = float(deriv2) / num_dev_set_words
    d3_deriv[order] = float(deriv3) / num_dev_set_words


def DiscountCountsOrder1():
    command = "discount-counts-1gram {num_words} <{work}/discounted.1 >{work}/float.1".format(
        num_words = num_words, work = args.work_dir)
    RunCommand(command)

def DiscountCountsOrder1Backward():
    command = ("discount-counts-1gram-backward {work}/discounted.1 {work}/float.1 "
               "{work}/float_derivs.1 {work}/discounted_derivs.1".format(work = args.work_dir))
    RunCommand(command)

def MergeAllOrders():
    command = ("merge-float-counts " +
               " ".join([ "{0}/float.{1}".format(args.work_dir, n) for n in range(1, ngram_order + 1) ])
               + ">{0}/float.all".format(args.work_dir))
    RunCommand(command)

def ComputeObjfAndFinalDerivs(need_derivs):
    global num_dev_set_words, objf
    command = "compute-probs {work}/float.all {counts}/int.dev ".format(
        work = args.work_dir, counts = args.count_dir);
    if need_derivs:
        command +=" ".join([ "{work}/float_derivs.{order}".format(work = args.work_dir, order = n)
                             for n in range(1, ngram_order + 1) ])
    output = GetCommandStdout(command)
    try:
        [ num_dev_set_words, tot_objf ] = output.split()
        num_dev_set_words = int(num_dev_set_words)
        objf = float(tot_objf) / num_dev_set_words
    except:
        sys.exit("get_objf_and_derivs.py: error interpreting the output of compute-probs: "
                 "output was: " + output)
    print("get_objf_and_derivs.py: objf is {0} over {1} "
          "words".format(objf, num_dev_set_words), file=sys.stderr)
    # Write the objective function.
    try:
        f = open(args.objf_out, "w")
        print(str(objf), file=f)
        f.close()
    except:
        sys.exit("get_objf_and_derivs.py: error writing objective function to: " +
                 args.objf_out)

def WriteDerivs():
    try:
        f = open(args.derivs_out, "w")
    except:
        sys.exit("get_objf_and_derivs.py: error opening --derivs-out={0} for writing".format(
                 args.derivs_out))
    for n in range(num_train_sets):
        print("count_scale_{0} {1}".format(n + 1, scale_derivs[n]), file=f)
    for o in range(2, ngram_order + 1):
        print("order{0}_D1 {1}".format(o, d1_deriv[o]), file=f)
        print("order{0}_D2 {1}".format(o, d2_deriv[o]), file=f)
        print("order{0}_D3 {1}".format(o, d3_deriv[o]), file=f)
    f.close()

# for n-gram orders down to 2, do the merging and discounting.
for o in range(ngram_order, 1, -1):
    MergeCounts(o)
    DiscountCounts(o)

DiscountCountsOrder1()
MergeAllOrders()
ComputeObjfAndFinalDerivs(args.derivs_out != None)

if args.derivs_out == None:
    sys.exit(0)

# scale_derivs will be an array of the derivatives of the objective function
# w.r.t. the scaling factors of the training sets.
scale_derivs = [ 0 ] * num_train_sets
# the following dicts will be indexed by the order.
d1_deriv = {}
d2_deriv = {}
d3_deriv = {}

# Now comes the backprop code.

# Note: there is no need for a call like MergeAllOrdersBackward(), because that
# merging was just aggregating different histories of distinct orders, and to
# avoid the need for a backprop version of this program, the program
# 'compute-probs' writes the derivatives for history-states of distinct orders,
# to distinct files.

DiscountCountsOrder1Backward()

for o in range(2, ngram_order + 1):
    DiscountCountsBackward(o)
    MergeCountsBackward(o)

WriteDerivs()