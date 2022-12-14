"""
data_initializer.py

This module prepares images for training of neural networks. Currently supported types include: 
 a) A directory of images + a directory of masks with the same file names. 
 b) A directory of images + an excel of files names and labels. 

The module splits the data into training and test sets (with matched distributions of label 
categories in the labels case), and does preprocessing. The default behavior allows for a one-line 
import, but detailed custom configuration can also be done. 

Execution:
    Import the ImageMasksDataset or ImageLabelDataset providing image and data paths to them. 
"""

from cmath import inf
from sklearn.utils import shuffle
from sklearn.model_selection import train_test_split
import tensorflow as tf
import cv2
import numpy as np
import os
from shared_utils.os_utils import list_dir
import math
import pandas as pd

class Dataset: 
    def __init__(self,path_images=''): 
        """
        This class is used to initialize the training data, define pre-processing and set behavior (batch size, train/val split, prefetching). Do not import directly - call its child classes to fully define an object useful for a specific training task.  

        Inputs:
            data_path (str): Top path expected to contain an \images sub directory. 

            path_images (str, optional): Direct path to training images. 

        Outputs: 
            A partially-defined class object which contains paths to images and training parameters. 
        """
        self.images, self.path_images = self.make_image_dirs(path_images)
        self.set_image_dims_original()
        self.image_dims_target = self.image_dims_original
        self.batch_size = 16
        self.set_seed(42) 

    def make_image_dirs(self,path_images): 
        """
        Function for getting paths to images used for training. 

        Inputs:
            self.data_path (str): Path to directory containing images as a subdirectory. Will be used to define a default path to images if path_images is not provided. 
            
            path_images (str, optional): Direct path to subdirectory containing images. 

        Outputs: 
            images (list): List of full paths to images to be used for training. 
            path_images (str): Path to the directory containing the images. 
        """
        images = self.get_image_list(path_images)
        num_examples = len(images)
        if hasattr(self, 'num_images'): 
            if num_examples != self.num_examples: 
                raise(OSError('Number of images and labels mismatched. Curate datapoints.'))
        self.num_examples = num_examples
        return images, path_images

    def get_mean_file_size(self): 
        """ Function for getting mean file size of the input image. 
        """
        file_paths = []
        for path in self.images: 
            file_paths.append(os.path.getsize(path))
        return sum(file_paths)/len(file_paths)
    
    def get_image_list(self,path:str): 
        """ Function to obtain paths to training images, and check that they were found. 
        """
        images = list_dir(path,target_type='files')
        if not images: 
            raise(FileNotFoundError('Cannot find image files in: ' + images))
        return images
        
    def set_image_target_dims(self,dims:tuple): 
        """ Function to set desired dimensions that training images (and masks) will be rescaled to. 
        """
        if len(dims) != 3: 
            raise(ValueError('Specify image dimensions as a tuple of (Height,Width,Channels).'))

        if min(dims[0:2]) < 100 and max(dims[0:2]) >10000: 
            print('Unexpected image size: ' + str(dims[0]) + 'x' + str(dims[1]))
            self.image_dims_target = dims
        self.image_dims_target = dims
    
    def set_image_dims_original(self): 
        """
        Read a sample image and use its dimensions to set dimensions for the dataset. All images are assumed to be of the same size. 
        """
        image_path = self.images[0]
        image = cv2.imread(image_path, cv2.IMREAD_COLOR)
        dims = image.shape
        if len(dims) <2 or len(dims)>3: 
            raise(ValueError('Unsupported image shape of ' + str(dims) + 'in ' + image_path))

        if len(dims) == 2: 
            self.image_dims_original = (dims[0],dims[1],1)
        else: 
            self.image_dims_original= dims
    
    def get_batch_max_batch_size_for_system_memory(self,memory_factor=0.8): 
        """ Function for setting the batch size to maximum allowed by system RAM. 
        NOTE: This is the method that should yield fastest training but may be prone to local minima. The alternative is small-batch or minibatch stochastic optimization (e.g. 32)
        TODO: Make separate function for use with GPU, which is currently overflowing. 
        """
        gpus = tf.config.list_physical_devices('GPU') # NOTE: Only tested on 1 GPU
        if gpus: 
            # If a GPU is visible at this stage - it will be used by tensorflow 
            memory_stats = tf.config.experimental.get_memory_info('GPU:0')
            available = memory_stats
        else: 
            import psutil
            available_memory = memory_factor * psutil.virtual_memory().available 
            available_memory = max([available_memory,50000000000]) # Set to 50 GB max to be respectful to shared system
            avg_file_size = self.get_mean_file_size()
            max_batch_size =  available_memory / avg_file_size
        return max_batch_size

    def __set_batch_size(self,batch_size): 
        """
        Set batch size to the nearest higher power of 2 of the specified number, unless overridden. This is more computationally efficient than using arbitrary sizes. 
        """
        fraction_of_dataset = round(self.num_examples / 10)
        if batch_size > fraction_of_dataset: 
            print('Batch size of ' + str(batch_size) + ' exceeds 10 percent of the dataset. \
             Typically, minibatch optimization is suggested for stability wrt local minima.')
        
        max_batch_size = self.get_batch_max_batch_size_for_system_memory()
        nearest_power_of_two = self.calc_nearest_power_of_two(batch_size,max_batch_size)
        new_batch_size = min([nearest_power_of_two,self.num_examples]) # Cap at actual number of examples
        if new_batch_size != batch_size: 
            print('Setting batch to nearest power of 2 for computational efficiency: ' + str(new_batch_size))
        self.batch_size = new_batch_size 

    def set_batch_size(self,batch_size): 
        self.__set_batch_size(batch_size)
        self.calc_train_steps()

    def set_max_batch_size(self): 
        self.set_batch_size(self.num_examples)
    
    def calc_train_steps(self): 
        """ Calculate the number of training steps based on batch and dataset size

        Inputs: 
            self.train_dataset (tf.dataset): The training data in tf.dataset form
            self.valid_dataset (tf.dataset): The validation data in tf.dataset form
            self.batch_size (int): The size of batches to use in training

        Outputs:
            self.train_steps (int): The number of training steps to run
            self.valid_steps (int): The number of validation steps to run
        """
        train_x = self.train_dataset
        valid_x = self.valid_dataset
        batch_size = self.batch_size

        self.train_steps = round((len(train_x)/batch_size))
        self.valid_steps = round((len(valid_x)/batch_size))

        if len(train_x) % batch_size != 0:
            self.train_steps += 1

        if len(valid_x) % batch_size != 0:
            self.valid_steps += 1 

    def calc_nearest_power_of_two(self,batch_size,limit=inf): 
        """ Round the batch_size down to nearest higher power of two for computational efficiency e.g. 60->64. Optionally, set limit. If exceeded, will use nearest lower power of 2. 
        """
        power_of_two = 2**math.ceil(math.log2(batch_size))
        if power_of_two > limit: 
            power_of_two = 2**math.floor(math.log2(batch_size))
        if power_of_two > limit: 
            raise(ValueError('Error - specified value ' + str(batch_size) + ' exceeds limit ' + str(limit)))
        return power_of_two

    def shuffling(self,x, y):
        x, y = shuffle(x, y, random_state=42)

    def read_image(self,path,image_type='image'):
        """
        Read one image, resize to target dims, and normalize by 255. Designed for color images with 3x 0-255 channels. 
        TODO: Extend to grayscle and validate. 
        """
        if not isinstance(path,str): 
            path = path.decode()
        if image_type == 'image': 
            x = cv2.imread(path, cv2.IMREAD_COLOR)
        elif image_type == 'mask': 
            x = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        else: 
            raise(ValueError("Trying to read unrecognized image type. Should be 'image' or 'mask'"))
        
        if self.image_dims_original != self.image_dims_target: 
            x = cv2.resize(x, (self.dims[0], self.dims[1]))
        x = x/255.0
        x = x.astype(np.float32)
        if len(x.shape)==2: 
            x = np.expand_dims(x, axis=-1)
        return x

    def tf_parse(self, x, y):
        def _parse(x, y):
            x = self.read_image(x,image_type='image')
            y = self.read_image(y,image_type='mask')
            return x, y

        x, y = tf.numpy_function(_parse, [x, y], [tf.float32, tf.float32])
        x.set_shape([self.image_dims_original[0], self.image_dims_original[1], 3])
        y.set_shape([self.image_dims_original[0], self.image_dims_original[1], 1])
        return x, y

    def tf_dataset(self, train_x, train_y):
        """ Create a fetcher for the training data with defined batch size. Use prefetching for computational efficiency. 
        """
        dataset = tf.data.Dataset.from_tensor_slices((train_x, train_y))
        # Preprocess images and masks
        dataset = dataset.map(self.tf_parse)
        dataset = dataset.batch(self.batch_size)
        dataset = dataset.prefetch(tf.data.AUTOTUNE)
        return dataset
    
    def set_seed(self,seed): 
        """ Set a seed for the train/val random split to make validation results reproducible. 
        """
        self.seed = seed
        np.random.seed(seed)
        tf.random.set_seed(seed)

    # def visualize_pos_neg_value_split(self): 
    #     # Placeholder to visualize split of classes. If significantly imbalanced - use balanced/weighted loss function
    #     train['label'].value_counts() / len(train)


class ImgMaskDataset(Dataset): 
    """
    Makes an object containing paths to paired masks/images together with pre-processing, and fetching functions that will be used to make batches at train time. 
    TODO: Make data loading a function reference, like it is for ImgLabelDataset to minimize memory consumption. 
    """
    def __init__(self,paths_training): 
        """
        Inputs:
            data_path (str): Top path expected to contain an \images sub directory. 

            image_path (str, optional): Direct path to training images. 

            mask_path (str, optional): Direct path to training masks. 

        Outputs: 
            A class object used by the model_initializer.py for training. 
        """
        for key in ['path_images','path_masks']: 
            if key not in paths_training: 
                raise(OSError('Required key missing ' + key))

        Dataset.__init__(self,paths_training['path_images'])
        # Initializes self.path_masks, self.masks 
        self.masks, self.path_masks = self.make_image_dirs(paths_training['path_masks'])

        self.prep_data_img_labels()

    def prep_data_img_labels(self):
        """ Function to prepare the training dataset. 
        Inputs: 
            self.images (list): Full paths to images. 

            self.masks (list): Full paths to masks. 
        Outputs: 
            self.train_dataset (tf.dataset): A dataset with paired images and labels. 
        """
        (train_x, train_y), (valid_x, valid_y), (test_x, test_y) = self.load_data(self.images,self.masks)
        train_x, train_y = shuffle(train_x, train_y)

        print(f"Train: {len(train_x)} - {len(train_y)}")
        print(f"Valid: {len(valid_x)} - {len(valid_y)}")
        print(f"Test: {len(test_x)} - {len(test_y)}")

        self.train_dataset = self.tf_dataset(train_x, train_y)
        self.valid_dataset = self.tf_dataset(valid_x, valid_y)
        self.calc_train_steps()
    
    def load_data(self, images, masks, split=0.2):
        """ Function to load the data and split into train and val sets. 
        """
        size = int(len(images) * split)

        # Split data into train and val sets
        train_x, valid_x = train_test_split(images, test_size=size, random_state=self.seed)
        train_y, valid_y = train_test_split(masks, test_size=size, random_state=self.seed)

        # Further split off a test set from train
        train_x, test_x = train_test_split(train_x, test_size=size, random_state=self.seed)
        train_y, test_y = train_test_split(train_y, test_size=size, random_state=self.seed)

        return (train_x, train_y), (valid_x, valid_y), (test_x, test_y)


class ImgLabelDataset(Dataset): 
    """
    Makes an object containing paths to images, and an excel with labels and corresponding image names together with pre-processing, and fetching functions that will be used to make batches at train time. 
    """

    def __init__(self,paths_training): 
        """
        Inputs:
            data_path (str): Top path expected to contain an \images sub directory. 

            image_path (str, optional): Direct path to training images. 

            path_labels (str, optional): Direct path to excel file with labels/corresponding image names.

        Outputs: 
            A class object used by the model_initializer.py for training. 
        """
        for key in ['path_images','path_labels']: 
            if key not in paths_training: 
                raise(OSError('Required key missing ' + key))
        Dataset.__init__(self,paths_training['path_images'])
        # Initializes self.data_labels 
        self.read_labels_from_excel(paths_training['path_labels'])
        self.train_val_split()
        self.calc_train_steps()

    def read_labels_from_excel(self,path_labels): 
        """ Reads labels from csv file

        Inputs:
            self.data_path (str): Top path expected to contain a labels.csv file.  

            path_labels (str, optional): Direct path to excel file with labels/corresponding image names.

        Outputs: 
            self.data_labels (pandas dataframe): Data labels and their ID's which are used to match to input images. 
        """
        data_labels = pd.read_csv(path_labels, dtype=str)
        data_labels.id = data_labels.id + '.tif'
        self.data_labels = data_labels
        if len(data_labels) != len(self.images): 
            raise(OSError('Number of images and labels mismatched. Curate datapoints.'))
        
    def train_val_split(self): 
        """ Split the training data into train and val sets, keeping the ratio of classes the same as in the population. 

        Inputs:
            self.data_labels (pandas dataframe): Data labels and their ID's which are used to match to input images.             

        Outputs: 
            self.train_dataset (pandas dataframe): Training labels and their filenames. 

            self.valid_dataset (pandas dataframe): Validation labels and their filenames. 
        """
        from sklearn.model_selection import train_test_split
        train_df, valid_df = train_test_split(self.data_labels, test_size=0.2, random_state=self.seed, stratify=self.data_labels.label) 

        self.train_dataset, TR_STEPS = self.make_random_data_generator(train_df)
        self.valid_dataset, VA_STEPS = self.make_random_data_generator(valid_df)

    def make_random_data_generator(self,df):
        """ Make a loader which yields random permutations of images and corresponding labels.

        Inputs:
            self.path_images (str): Path to image files. 
            
            df (pandas dataframe): Labels and filenames of associated images. 

        Outputs: 
            loader (function reference): A data loader function for fetching images at runtime. 

        """
        from tensorflow.keras.preprocessing.image import ImageDataGenerator
        datagen = ImageDataGenerator(rescale=1/255)
        # Make a training data loader which yields random perumtations of training data with a given batch size
        image_path = self.path_images
        loader = datagen.flow_from_dataframe(
            dataframe = df,
            directory = image_path,
            x_col = 'id',
            y_col = 'label',
            batch_size = self.batch_size,
            seed = 1,
            shuffle = True,
            class_mode = 'categorical',
            target_size = (96,96)
        )
        
        num_steps = len(loader)
        return loader, num_steps

# class Augment(tf.keras.layers.Layer):
#   # Use train_batches = (train_images.cache().shuffle(BUFFER_SIZE).batch(BATCH_SIZE).repeat().map(Augment()).prefetch(buffer_size=tf.data.AUTOTUNE))
#   # test_batches = test_images.batch(BATCH_SIZE)
#   def __init__(self, seed=42):
#     super().__init__()
#     # both use the same seed, so they'll make the same random changes.
#     self.seed = seed
#     self.augment_inputs = tf.keras.layers.RandomFlip(mode="horizontal", seed=self.seed)
#     self.augment_labels = tf.keras.layers.RandomFlip(mode="horizontal", seed=self.seed)

#   def call(self, inputs, labels):
#     inputs = self.augment_inputs(inputs)
#     labels = self.augment_labels(labels)
#     return inputs, labels

# if __name__ == "__main__":
#     main()
