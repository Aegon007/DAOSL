import data as dataset
import numpy as np
import os
import sys
import tensorflow as tf
import tensorflow.contrib.slim as slim
import tqdm
from config import * 
from ada import ada

if not os.path.exists(args.snapshot_dir):
  os.makedirs(args.snapshot_dir)

# Experiment Setup
height = args.input_size
width = args.input_size
classes_per_set = args.num_ways
samples_per_class = args.num_shots
continue_from_epoch = args.ckpt  # use -1 to start from scratch

experiment_name = args.exp_name

num_epochs = args.num_epochs
num_train_episodes = 2000
num_val_episodes = 1000

data = dataset.CharDataset(
  classes_per_set=classes_per_set, 
  samples_per_class=samples_per_class,
  seed=args.random_seed,
  source_file=args.source+'.npy',
  target_file=args.target+'.npy')
channels = 1

# Build Graph
train_support_images = tf.placeholder(tf.float32, [classes_per_set, 
  samples_per_class, height, width, channels])
train_support_labels = tf.placeholder(tf.int32, [classes_per_set, 
  samples_per_class, 1])
train_query_image = tf.placeholder(tf.float32, [1, height, width, channels])
train_query_label = tf.placeholder(tf.int32, [1])

val_support_images = tf.placeholder(tf.float32, [classes_per_set, 
  samples_per_class, height, width, channels])
val_support_labels = tf.placeholder(tf.int32, [classes_per_set, 
  samples_per_class, 1])
val_query_image = tf.placeholder(tf.float32, [1, height, width, channels])
val_query_label = tf.placeholder(tf.int32, [1])

accuracy, disc_loss, cls_loss = ada(
  train_support_images, 
  train_support_labels, 
  train_query_image, 
  train_query_label,
  val_support_images,
  is_training=True)

val_accuracy, val_disc_loss, val_cls_loss = ada(
  val_support_images, 
  val_support_labels, 
  val_query_image, 
  val_query_label,
  val_support_images,
  reuse=True,
  is_training=False)

# Optimization
train_variables = [v for v in tf.trainable_variables()]
disc_vars = [var for var in train_variables if 'disc' in var.name]
cls_vars = [var for var in train_variables if 'disc' not in var.name]

global_step = tf.train.get_or_create_global_step()
learning_rate = tf.constant(args.learning_rate)

opt_disc = tf.train.AdamOptimizer(learning_rate=learning_rate)
opt_cls = tf.train.AdamOptimizer(learning_rate=learning_rate)
        
train_op_disc = slim.learning.create_train_op(
  total_loss=disc_loss,
  optimizer=opt_disc,
  global_step=global_step,
  variables_to_train=disc_vars)

train_op_cls = slim.learning.create_train_op(
  total_loss=cls_loss,
  optimizer=opt_cls,
  global_step=global_step,
  variables_to_train=cls_vars)

# Build Session
init = tf.global_variables_initializer()
tf_config = tf.ConfigProto()
tf_config.gpu_options.allow_growth = True
with tf.Session(config=tf_config) as sess:
  sess.run(init)
  saver = tf.train.Saver(var_list=tf.global_variables(), max_to_keep=20)
  
  if continue_from_epoch != -1: #load checkpoint if needed
    checkpoint = "{}/{}_{}.ckpt".format(args.snapshot_dir, experiment_name, 
      continue_from_epoch)
    variables_to_restore = tf.global_variables()
    fine_tune = slim.assign_from_checkpoint_fn(
      checkpoint,
      variables_to_restore,
      ignore_missing_vars=True)
    fine_tune(sess)

  with tqdm.tqdm(total=num_epochs) as pbar_e:
    for e in range(args.ckpt+1, num_epochs):
      # Training Phase
      total_disc_loss = []
      total_cls_loss = []
      total_accuracy = []
      with tqdm.tqdm(total=num_train_episodes) as pbar:
        for i in range(num_train_episodes):  # train epoch
          x_support_set, y_support_set, x_query, y_query = \
            data.get_train_batch(augment=False)
          val_x_support_set, val_y_support_set, val_x_query, val_y_query = \
            data.get_val_batch(
          augment=False)

          for _ in range(args.num_d_iters):
            sess.run([train_op_disc], 
              feed_dict={
                train_support_images: x_support_set,
                train_support_labels: y_support_set, 
                train_query_image: x_query, 
                train_query_label: y_query,
                val_support_images: val_x_support_set,
                val_support_labels: val_y_support_set, 
                val_query_image: val_x_query, 
                val_query_label: val_y_query})
          
          for _ in range(args.num_g_iters):
            sess.run([train_op_cls],
              feed_dict={
                train_support_images: x_support_set,
                train_support_labels: y_support_set, 
                train_query_image: x_query, 
                train_query_label: y_query,
                val_support_images: val_x_support_set,
                val_support_labels: val_y_support_set, 
                val_query_image: val_x_query, 
                val_query_label: val_y_query})
          
          disc_loss_float, cls_loss_float, acc_float = sess.run([disc_loss,
            cls_loss, accuracy],
            feed_dict={
              train_support_images: x_support_set,
              train_support_labels: y_support_set, 
              train_query_image: x_query, 
              train_query_label: y_query,
              val_support_images: val_x_support_set,
              val_support_labels: val_y_support_set, 
              val_query_image: val_x_query, 
              val_query_label: val_y_query})
          
          total_disc_loss.append(disc_loss_float)
          total_cls_loss.append(cls_loss_float)
          total_accuracy.append(acc_float)

          pbar.update(1)
      
      epoch_out = "\nEpoch {}: disc_loss: {:.4f}, cls_loss: {:.4f}, train_accuracy: {:.4f}".format(e, np.mean(total_disc_loss), np.mean(total_cls_loss), np.mean(total_accuracy))

      pbar_e.set_description(epoch_out)
      pbar_e.update(1)
    
    # Validation Phase      
    val_total_acc = []
    for j in range(100):
      val_acc = []
      for i in range(num_val_episodes):
        x_support_set, y_support_set, x_query, y_query = data.get_val_batch(
          augment=False)
        [val_acc_float] = sess.run([val_accuracy],
          feed_dict={
            val_support_images: x_support_set,
            val_support_labels: y_support_set, 
            val_query_image: x_query, 
            val_query_label: y_query})

        val_acc.append(val_acc_float)
      val_total_acc.append(np.mean(val_acc))

    print("\nmean: {:.4f}, std: {:.4f}".format(np.mean(val_total_acc), 
      np.std(val_total_acc)))
    saver.save(sess, "{}/{}_{}.ckpt".format(args.snapshot_dir, 
      experiment_name, e))