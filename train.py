import numpy as np
import tensorflow as tf
import os
import pdb
import random
import sys
import argparse
from glob import glob
import lib.metrics
import lib.dataset
import lib.evaluation

print(f"Numpy version: {np.__version__}")
print(f"Tensorflow version: {tf.__version__}")

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
random.seed(432)

# Various loading and saving constants.
default_train_dir = "./data/eyepacs/bin2/train"
default_val_dir = "./data/eyepacs/bin2/validation"
default_save_model_path = "./tmp/model"
default_save_summaries_dir = "./tmp/logs"

parser = argparse.ArgumentParser(
                    description="Trains and saves neural network for "
                                "detection of diabetic retinopathy.")
parser.add_argument("-t", "--train_dir",
                    help="path to folder that contains training tfrecords",
                    default=default_train_dir)
parser.add_argument("-v", "--val_dir",
                    help="path to folder that contains validation tfrecords",
                    default=default_val_dir)
parser.add_argument("-sm", "--save_model_path",
                    help="path to where graph model should be saved",
                    default=default_save_model_path)
parser.add_argument("-ss", "--save_summaries_dir",
                    help="path to folder where summaries should be saved",
                    default=default_save_summaries_dir)
parser.add_argument("-sgd", "--vanilla_sgd", action="store_true",
                    help="use vanilla stochastic gradient descent instead of "
                         "nesterov accelerated gradient descent")

args = parser.parse_args()
train_dir = str(args.train_dir)
val_dir = str(args.val_dir)
save_model_path = str(args.save_model_path)
save_summaries_dir = str(args.save_summaries_dir)
use_sgd = bool(args.vanilla_sgd)

# Various constants.
num_channels = 3
num_workers = 8

# Hyper-parameters.
num_epochs = 200
wait_epochs = 10
learning_rate = 3e-3
momentum = 0.9  # Only used if use_sgd is False
use_nesterov = True  # Only used if use_sgd is False

# Batch sizes.
train_batch_size = 32
val_batch_size = 32

# Buffer size for image shuffling.
shuffle_buffer_size = 500
prefetch_buffer_size = 10 * train_batch_size

# Set image datas format to channels first if GPU is available.
if tf.test.is_gpu_available():
    print("Found GPU! Using channels first as default image data format.")
    image_data_format = 'channels_first'
else:
    image_data_format = 'channels_last'

# Set up a session and bind it to Keras.
sess = tf.Session()
tf.keras.backend.set_session(sess)
tf.keras.backend.set_learning_phase(True)
tf.keras.backend.set_image_data_format(image_data_format)

# Initialize each data set.
train_dataset = lib.dataset.initialize_dataset(
    train_dir, train_batch_size,
    num_workers=num_workers, prefetch_buffer_size=prefetch_buffer_size,
    shuffle_buffer_size=shuffle_buffer_size,
    image_data_format=image_data_format, num_channels=num_channels)

val_dataset = lib.dataset.initialize_dataset(
    val_dir, val_batch_size,
    num_workers=num_workers, prefetch_buffer_size=prefetch_buffer_size,
    shuffle_buffer_size=shuffle_buffer_size,
    image_data_format=image_data_format, num_channels=num_channels)

# Create initializable iterators.
iterator = tf.data.Iterator.from_structure(
    train_dataset.output_types, train_dataset.output_shapes)

images, labels = iterator.get_next()
x = tf.placeholder_with_default(images, images.shape, name='x')
y = tf.placeholder_with_default(labels, labels.shape, name='y')

train_init_op = iterator.make_initializer(train_dataset)
val_init_op = iterator.make_initializer(val_dataset)

# Base model InceptionV3 without top and global average pooling.
base_model = tf.keras.applications.InceptionV3(
    include_top=False, weights='imagenet', pooling='avg', input_tensor=x)

# Add dense layer with the same amount of neurons as labels.
with tf.name_scope('logits'):
    logits = tf.layers.dense(base_model.output, units=1)

# Get the predictions with a sigmoid activation function.
with tf.name_scope('predictions'):
    predictions = tf.sigmoid(logits)

# Get the class predictions for labels.
predictions_classes = tf.round(predictions)

# Retrieve loss of network using cross entropy.
mean_xentropy = tf.reduce_mean(
    tf.nn.sigmoid_cross_entropy_with_logits(labels=y, logits=logits))

# Define SGD optimizer with momentum and nesterov.
global_step = tf.Variable(0, dtype=tf.int32)

if use_sgd
    train_op = tf.train.GradientDescentOptimizer(learning_rate) \
        .minimize(loss=mean_xentropy, global_step=global_step)
else:
    train_op = tf.train.MomentumOptimizer(
        learning_rate, momentum=momentum, use_nesterov=use_nesterov) \
            .minimize(loss=mean_xentropy, global_step=global_step)

# Metrics for finding best validation set.
tp, update_tp, reset_tp = lib.metrics.create_reset_metric(
    lib.metrics.true_positives, scope='tp', labels=y,
    predictions=predictions_classes)

fp, update_fp, reset_fp = lib.metrics.create_reset_metric(
    lib.metrics.false_positives, scope='fp', labels=y,
    predictions=predictions_classes)

fn, update_fn, reset_fn = lib.metrics.create_reset_metric(
    lib.metrics.false_negatives, scope='fn', labels=y,
    predictions=predictions_classes)

tn, update_tn, reset_tn = lib.metrics.create_reset_metric(
    lib.metrics.true_negatives, scope='tn', labels=y,
    predictions=predictions_classes)

confusion_matrix = lib.metrics.confusion_matrix(
    tp, fp, fn, tn, scope='confusion_matrix')

brier, update_brier, reset_brier = lib.metrics.create_reset_metric(
    tf.metrics.mean_squared_error, scope='brier',
    labels=y, predictions=predictions)

auc, update_auc, reset_auc = lib.metrics.create_reset_metric(
    tf.metrics.auc, scope='auc',
    labels=y, predictions=predictions)
tf.summary.scalar('auc', auc)

# Merge all the summaries and write them out.
summaries_op = tf.summary.merge_all()
train_writer = tf.summary.FileWriter(save_summaries_dir + "/train")
test_writer = tf.summary.FileWriter(save_summaries_dir + "/test")

def print_training_status(epoch, num_epochs, batch_num, xent, i_step=None):
    def length(x): return len(str(x))

    m = []
    m.append(
        f"Epoch: {{0:>{length(num_epochs)}}}/{{1:>{length(num_epochs)}}}"
        .format(epoch, num_epochs))
    m.append(f"Batch: {batch_num:>4}, Xent: {xent:6.4}")

    if i_step is not None:
        m.append(f"Step: {i_step:>10}")

    print(", ".join(m), end="\r")


# Add ops for saving and restoring all variables.
saver = tf.train.Saver()

# Initialize variables.
sess.run(tf.global_variables_initializer())
sess.run(tf.local_variables_initializer())

# Train for the specified amount of epochs.
# Can be stopped early if peak of validation auc (Area under curve)
#  is reached.
latest_peak_auc = 0
waited_epochs = 0

for epoch in range(num_epochs):
    # Start training.
    tf.keras.backend.set_learning_phase(True)
    sess.run(train_init_op)
    batch_num = 0

    try:
        while True:
            # Optimize cross entropy.
            i_global, batch_xent, _ = sess.run(
                [global_step, mean_xentropy, train_op])

            # Print a nice training status.
            print_training_status(
                epoch, num_epochs, batch_num, batch_xent, i_global)

            # Report summaries.
            batch_num += 1

    except tf.errors.OutOfRangeError:
        print(f"\nEnd of epoch {epoch}!")

    # Perform validation.
    val_auc = lib.evaluation.perform_test(
        sess=sess, init_op=val_init_op,
        summary_writer=train_writer, epoch=epoch)

    if val_auc < latest_peak_auc:
        # Stop early if peak of val auc has been reached.
        # If it is lower than the previous auc value, wait up to `wait_epochs`
        #  to see if it does not increase again.

        if wait_epochs == waited_epochs:
            print("Stopped early at epoch {0} with saved peak auc {1:10.8}"
                  .format(epoch+1, latest_peak_auc))
            break

        waited_epochs += 1
    else:
        latest_peak_auc = val_auc
        print(f"New peak auc reached: {val_auc:10.8}")

        # Save the model weights.
        saver.save(sess, save_model_path)

        # Reset waited epochs.
        waited_epochs = 0

# Close the session.
sess.close()
sys.exit(0)
